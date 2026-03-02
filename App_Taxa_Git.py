import pandas as pd
import numpy as np
import streamlit as st
import datetime
from io import BytesIO
import re
import os
import requests

class CalculandoTaxadeGestao:
    def __init__(self):
        self.planilha_controle = None
        self.pl_data = []

    def load_control_file(self, uploaded_planilha_de_controle, broker):
        """Load the control spreadsheet for the specified broker tab."""
        try:
            self.planilha_controle = pd.read_excel(uploaded_planilha_de_controle, sheet_name=broker, skiprows=1)
            if broker == 'BTG':
                self.planilha_controle['Conta'] = self.planilha_controle['Conta'].astype(str).str.replace(r'\.0$', '', regex=True).apply(lambda x: x.zfill(9))
            elif broker in ['Safra', 'Ágora']:
                self.planilha_controle['Conta'] = self.planilha_controle['Conta'].astype(str).str.replace(r'\.0$', '', regex=True)
            
            # Filter out empty accounts or those with fewer than 5 digits
            self.planilha_controle = self.planilha_controle[
                (self.planilha_controle['Conta'].notna()) & 
                (self.planilha_controle['Conta'].str.strip() != '') & 
                (self.planilha_controle['Conta'].str.len() >= 5)
            ]
            
            self.planilha_controle = self.planilha_controle[['Cliente', 'Conta', 'Taxa de Gestão']]
            self.planilha_controle.rename(columns={'Taxa de Gestão': 'Taxa_de_Gestão', 'Conta': 'conta'}, inplace=True)
        except Exception as e:
            st.error(f"Erro ao carregar planilha de controle (aba {broker}): {e}")
            return False
        return True

    def load_pl_file(self, uploaded_pl, file_name, broker, year):
        """Load a PL file and extract date from file name."""
        try:
            match = re.search(r'(\d{2}\.\d{2})\.xlsx$', file_name)
            if not match:
                st.error(f"Nome do arquivo '{file_name}' não contém data no formato 'DD.MM.xlsx'. Pulando arquivo.")
                return False
            date_str = match.group(1)
            try:
                # Parse date with explicit year
                date = datetime.datetime.strptime(f"{date_str}.{year}", '%d.%m.%Y')
            except ValueError:
                st.error(f"Data inválida no nome do arquivo '{file_name}'. Use o formato 'DD.MM.xlsx' com um ano válido. Pulando arquivo.")
                return False

            if broker == 'Safra':
                pl = pd.read_excel(uploaded_pl, skiprows=2)
                pl = pl[pl['Ativo'] != 'RDVT13']
                pl['PL'] = pl['PL'].astype(str).str.replace(',', '').astype(float)
                pl = pl.groupby('Conta', as_index=False)['PL'].sum()
                pl['Conta'] = pl['Conta'].astype(str).str.replace(r'\.0$', '', regex=True)
                # Filter out empty accounts or those with fewer than 5 digits
                pl = pl[
                    (pl['Conta'].notna()) & 
                    (pl['Conta'].str.strip() != '') & 
                    (pl['Conta'].str.len() >= 5)
                ]
                pl = pl[['Conta', 'PL']].rename(columns={'Conta': 'conta', 'PL': 'VALOR'})
            elif broker == 'BTG':
                pl = pd.read_excel(uploaded_pl)
                pl['Conta'] = pl['Conta'].astype(str).str.replace(r'\.0$', '', regex=True).apply(lambda x: x.zfill(9))
                # Filter out empty accounts or those with fewer than 5 digits
                pl = pl[
                    (pl['Conta'].notna()) & 
                    (pl['Conta'].str.strip() != '') & 
                    (pl['Conta'].str.len() >= 5)
                ]
                pl = pl[['Conta', 'Valor']].rename(columns={'Conta': 'conta', 'Valor': 'VALOR'})
            pl['Data'] = date
            self.pl_data.append(pl)
            return True
        except Exception as e:
            st.error(f"Erro ao carregar arquivo PL '{file_name}': {e}")
            return False

    def calculate_daily_fees(self):
        """Calculate daily management fees and pivot to daily columns with PL and Taxa side by side."""
        if (
            self.planilha_controle is None
            or not isinstance(self.planilha_controle, pd.DataFrame)
            or self.planilha_controle.empty
            or len(self.pl_data) == 0
        ):
            st.error("Planilha de controle ou arquivos PL não carregados.")
            return None, None, None

        pl_combined = pd.concat(self.pl_data, ignore_index=True)
        # Remove duplicates based on 'conta' and 'Data' to prevent duplicate date columns
        pl_combined = pl_combined.drop_duplicates(subset=['conta', 'Data'], keep='last')
        calculo_diario = 1/252

        tx_gestao = pd.merge(self.planilha_controle, pl_combined, left_on='conta', right_on='conta', how='outer')
        tx_gestao = tx_gestao[['Cliente', 'conta', 'Taxa_de_Gestão', 'VALOR', 'Data']].dropna(subset=['conta'])

        unmatched_control = tx_gestao[tx_gestao['VALOR'].isna()]['conta'].unique()
        unmatched_pl = tx_gestao[tx_gestao['Taxa_de_Gestão'].isna()]['conta'].unique()

        tx_gestao['Tx_Gestão_Diaria'] = ((tx_gestao['Taxa_de_Gestão'] + 1) ** calculo_diario - 1) * 100
        tx_gestao['Valor_de_cobrança'] = round(tx_gestao['VALOR'] * (tx_gestao['Tx_Gestão_Diaria']) / 100, 2)

        tx_gestao['Data'] = pd.to_datetime(tx_gestao['Data']).dt.strftime('%d.%m')

        # Pivot for daily PL and Taxa, using 'first' to avoid duplicate columns
        pivot_table = tx_gestao.pivot_table(
            values=['VALOR', 'Valor_de_cobrança'],
            index=['Cliente', 'conta'],
            columns='Data',
            aggfunc='first'  # Use 'first' to take the first occurrence and avoid duplicate
        ).reset_index()

        # Flatten multi-level columns and rename
        pivot_table.columns = [f"{col[1]}_{col[0]}" if col[1] else col[0] for col in pivot_table.columns]
        pivot_table = pivot_table.rename(columns={col: col.replace('Valor_de_cobrança', 'Taxa') for col in pivot_table.columns})

        # Reorder columns to have PL and Taxa side by side
        dates = sorted(set([col.split('_')[0] for col in pivot_table.columns if '_' in col and re.match(r'^\d{2}\.\d{2}$', col.split('_')[0])]), key=lambda x: pd.to_datetime(x, format='%d.%m'))
        ordered_columns = ['Cliente', 'conta']
        for date in dates:
            if f"{date}_VALOR" in pivot_table.columns and f"{date}_Taxa" in pivot_table.columns:
                ordered_columns.append(f"{date}_VALOR")
                ordered_columns.append(f"{date}_Taxa")
        pivot_table = pivot_table[ordered_columns]

        # Calculate total PL and Taxa
        valor_columns = [col for col in pivot_table.columns if col.endswith('_VALOR')]
        taxa_columns = [col for col in pivot_table.columns if col.endswith('_Taxa')]
        pivot_table['PL_Total'] = pivot_table[valor_columns].sum(axis=1).round(2)
        pivot_table['Total_Taxa'] = pivot_table[taxa_columns].sum(axis=1).round(2)

        return pivot_table, unmatched_control, unmatched_pl

    def to_excel(self, df):
        """Convert DataFrame to Excel for download."""
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Taxa_Gestao_Diaria')
        return output.getvalue()

# Function to clean currency from Limpeza_planilha_Agora.py
def clean_currency(value):
    if isinstance(value, str):
        value = value.replace('R$', '').replace('.', '').replace(',', '.').strip()
        try:
            return float(value)
        except ValueError:
            return float('nan')
    return value

st.title("Cálculo de Taxa de Gestão Diária")

# Select processing type
st.subheader("Selecionar Tipo de Processamento")
processing_type = st.radio("Escolha a corretora para processar:", ("BTG", "Ágora", "Safra"))

# Year input for date parsing
st.subheader("Selecionar Ano dos Arquivos PL")
year = st.number_input("Digite o ano dos arquivos PL (ex.: 2025)", min_value=2000, max_value=2100, value=2025, step=1)

# Fetch control spreadsheet from GitHub instead of upload
st.subheader("Carregar Planilha de Controle")
control_url = "https://raw.githubusercontent.com/bluemetrixgit/TaxaDeGestao/main/Controle%20de%20Contratos%20-%20Atualizado%202026.xlsx"  # Substitua pela URL real do repositório GitHub
try:
    response = requests.get(control_url)
    response.raise_for_status()
    uploaded_control = BytesIO(response.content)
except Exception as e:
    st.error(f"Erro ao carregar planilha de controle do GitHub: {e}")
    uploaded_control = None

if processing_type == "BTG":
    st.subheader("Processar PL Diários do BTG e Calcular Taxas")
    calculadora = CalculandoTaxadeGestao()

    if uploaded_control:
        if calculadora.load_control_file(uploaded_control, 'BTG'):
            st.success("Planilha de controle (BTG) carregada com sucesso!")

    uploaded_pls_btg = st.file_uploader("Carregar Arquivos PL Diários do BTG", type=['xlsx'], accept_multiple_files=True)
    if uploaded_pls_btg:
        for pl_file in uploaded_pls_btg:
            calculadora.load_pl_file(pl_file, pl_file.name, 'BTG', year)
        st.success("Arquivos PL do BTG carregados com sucesso!")

    if st.button("Calcular Taxas Diárias do BTG"):
        result, unmatched_control, unmatched_pl = calculadora.calculate_daily_fees()
        if result is not None:
            excel_data = calculadora.to_excel(result)
            st.download_button(
                label="Baixar btg_taxa_gestao_diaria.xlsx",
                data=excel_data,
                file_name="btg_taxa_gestao_diaria.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            with st.expander("Contas Não Casadas"):
                if len(unmatched_control) > 0:
                    st.warning(f"Contas na planilha de controle (BTG) não encontradas nos arquivos PL: {', '.join(unmatched_control)}")
                else:
                    st.info("Todas as contas da planilha de controle (BTG) foram casadas.")
                if len(unmatched_pl) > 0:
                    st.warning(f"Contas nos arquivos PL não encontradas na planilha de controle (BTG): {', '.join(unmatched_pl)}")
                else:
                    st.info("Todas as contas dos arquivos PL foram casadas com a planilha de controle (BTG).")

elif processing_type == "Safra":
    st.subheader("Processar PL Diários do Safra e Calcular Taxas")
    calculadora = CalculandoTaxadeGestao()

    if uploaded_control:
        if calculadora.load_control_file(uploaded_control, 'Safra'):
            st.success("Planilha de controle (Safra) carregada com sucesso!")

    uploaded_pls_safra = st.file_uploader("Carregar Arquivos PL Diários do Safra", type=['xlsx'], accept_multiple_files=True)
    if uploaded_pls_safra:
        for pl_file in uploaded_pls_safra:
            calculadora.load_pl_file(pl_file, pl_file.name, 'Safra', year)
        st.success("Arquivos PL do Safra carregados com sucesso!")

    if st.button("Calcular Taxas Diárias do Safra"):
        result, unmatched_control, unmatched_pl = calculadora.calculate_daily_fees()
        if result is not None:
            excel_data = calculadora.to_excel(result)
            st.download_button(
                label="Baixar safra_taxa_gestao_diaria.xlsx",
                data=excel_data,
                file_name="safra_taxa_gestao_diaria.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            with st.expander("Contas Não Casadas"):
                if len(unmatched_control) > 0:
                    st.warning(f"Contas na planilha de controle (Safra) não encontradas nos arquivos PL: {', '.join(unmatched_control)}")
                else:
                    st.info("Todas as contas da planilha de controle (Safra) foram casadas.")
                if len(unmatched_pl) > 0:
                    st.warning(f"Contas nos arquivos PL não encontradas na planilha de controle (Safra): {', '.join(unmatched_pl)}")
                else:
                    st.info("Todas as contas dos arquivos PL foram casadas com a planilha de controle (Safra).")

elif processing_type == "Ágora":
    st.subheader("Processar PL Diários da Ágora")
    uploaded_pls_agora = st.file_uploader("Carregar Arquivos PL Diários da Ágora", type=['xlsx'], accept_multiple_files=True)

    if st.button("Gerar agora_total.xlsx") and uploaded_pls_agora:
        currency_columns = [
            'Ações/FIIs/ETFs/BDRs', 
            'Títulos privados', 
            'Títulos públicos', 
            'COE', 
            'Fundos e clubes de investimento', 
            'Opções', 
            'Ouro', 
            'Termo de Ações', 
            'Saldo projetado'
        ]
        dfs_agora = {}

        for pl_file in uploaded_pls_agora:
            try:
                df = pd.read_excel(pl_file, sheet_name='Sheet0')
                df = df.drop(columns=['Nome', 'CPF/CNPJ', 'Escritório', 'Barra', 'Data da Requisição'])
                for col in currency_columns:
                    if col in df.columns:
                        df[col] = df[col].apply(clean_currency)
                if 'CBLC' in df.columns:
                    df['CBLC'] = df['CBLC'].astype(str).str.replace('-', '').astype(float).astype(int)
                    # Filter out empty accounts or those with fewer than 5 digits
                    df = df[
                        (df['CBLC'].notna()) & 
                        (df['CBLC'].astype(str).str.strip() != '') & 
                        (df['CBLC'].astype(str).str.len() >= 5)
                    ]
                else:
                    st.error(f"Column 'CBLC' not found in {pl_file.name}")
                    continue
                df['PL'] = df[currency_columns].sum(axis=1)
                df = df[['CBLC', 'PL']]
                dfs_agora[pl_file.name] = df
                st.success(f"Processed Ágora file: {pl_file.name}")
            except Exception as e:
                st.error(f"Error processing Ágora file {pl_file.name}: {str(e)}")

        combined_df_agora = pd.DataFrame()
        for filename, df in dfs_agora.items():
            base_filename = os.path.splitext(filename)[0]
            df = df.set_index('CBLC')
            df = df.rename(columns={'PL': f'PL_{base_filename}'})
            if combined_df_agora.empty:
                combined_df_agora = df
            else:
                combined_df_agora = combined_df_agora.combine_first(df)

        combined_df_agora = combined_df_agora.reset_index()
        combined_df_agora = combined_df_agora.drop_duplicates(subset='CBLC', keep='last')
        pl_columns = [col for col in combined_df_agora.columns if col.startswith('PL_')]
        combined_df_agora['PL Total'] = combined_df_agora[pl_columns].sum(axis=1)
        combined_df_agora = combined_df_agora.rename(columns={'CBLC': 'Conta'})

        output = BytesIO()
        combined_df_agora.to_excel(output, index=False)
        output.seek(0)
        st.download_button(
            label="Baixar agora_total.xlsx",
            data=output,
            file_name="agora_total.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    if uploaded_control:
        dfs = {}
        try:
            df = pd.read_excel(uploaded_control, sheet_name='Ágora', skiprows=1, usecols=['Cliente', 'Conta', 'Taxa de Gestão'])
            df = df[df['Taxa de Gestão'].notna()]
            # Filter out empty accounts or those with fewer than 5 digits
            df = df[
                (df['Conta'].notna()) & 
                (df['Conta'].astype(str).str.strip() != '') & 
                (df['Conta'].astype(str).str.len() >= 5)
            ]
            df['Taxa Diária'] = (1 + df['Taxa de Gestão']) ** (1/252) - 1
            df['Taxa Diária'] = df['Taxa Diária'].map('{:.8f}'.format)
            dfs['Ágora'] = df
            st.success("Planilha de controle (Ágora) carregada com sucesso!")
        except Exception as e:
            st.error(f"Erro ao carregar aba Ágora da planilha de controle: {str(e)}")

    uploaded_agora = st.file_uploader("Carregar agora_total.xlsx", type=['xlsx'])
    if uploaded_control and uploaded_agora and 'Ágora' in dfs:
        try:
            agora_df = dfs['Ágora']
            agora_df['Conta'] = agora_df['Conta'].astype(str).str.replace(r'\.0$', '', regex=True).str.replace(r'\.$', '', regex=True)
            agora_total_df = pd.read_excel(uploaded_agora)
            agora_total_df['Conta'] = agora_total_df['Conta'].astype(str).str.lstrip('0')

            merged_df_agora = pd.merge(agora_df, agora_total_df, left_on='Conta', right_on='Conta', how='left')

            unmatched_control = set(agora_df['Conta']) - set(agora_total_df['Conta'])
            unmatched_pl = set(agora_total_df['Conta']) - set(agora_df['Conta'])

            pl_columns = [col for col in merged_df_agora.columns if col.startswith('PL_') and col != 'PL Total']
            for pl_col in pl_columns:
                date = pl_col.replace('PL_', '')
                merged_df_agora[f'Taxa_{date}'] = merged_df_agora[pl_col] * merged_df_agora['Taxa Diária'].astype(float)
                merged_df_agora[f'Taxa_{date}'] = merged_df_agora[f'Taxa_{date}'].round(2)

            # Reorder columns to have PL and Taxa side by side
            ordered_columns = ['Cliente', 'Conta', 'Taxa de Gestão', 'Taxa Diária']
            for col in pl_columns:
                date = col.replace('PL_', '')
                ordered_columns.append(col)  # PL_DD.MM
                ordered_columns.append(f'Taxa_{date}')  # Taxa_DD.MM
            ordered_columns.append('PL Total')
            ordered_columns.append('Taxa Total')
            merged_df_agora['Taxa Total'] = merged_df_agora[[col for col in merged_df_agora.columns if col.startswith('Taxa_')]].sum(axis=1).round(2)
            merged_df_agora = merged_df_agora[ordered_columns]

            output = BytesIO()
            merged_df_agora.to_excel(output, index=False)
            output.seek(0)
            st.download_button(
                label="Baixar agora_merged_with_taxa.xlsx",
                data=output,
                file_name="agora_merged_with_taxa.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            with st.expander("Contas Não Casadas"):
                if unmatched_control:
                    st.warning(f"Contas na planilha de controle (Ágora) não encontradas em agora_total.xlsx: {', '.join(unmatched_control)}")
                else:
                    st.info("Todas as contas da planilha de controle (Ágora) foram casadas.")
                if unmatched_pl:
                    st.warning(f"Contas em agora_total.xlsx não encontradas na planilha de controle (Ágora): {', '.join(unmatched_pl)}")
                else:
                    st.info("Todas as contas de agora_total.xlsx foram casadas com a planilha de controle (Ágora).")

        except Exception as e:

            st.error(f"Erro ao processar merge da Ágora: {str(e)}")
