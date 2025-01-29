import gradio as gr
from datetime import datetime
import sqlite3
import requests
import json
import os
import shutil  # Para remover arquivos

from langchain.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain.schema.runnable import RunnableMap

# =============================================================================
# CONFIGURAÇÃO DA CHAVE OPENAI
# =============================================================================
os.environ['OPENAI_API_KEY'] = 's'


# =============================================================================
# CLASSE DE GERENCIAMENTO GLOBAL DE USUÁRIOS (users.db)
# =============================================================================
class UserManager:
    """
    Armazena apenas dados de login (username, password, data_criacao)
    no arquivo 'users.db'.

    Cada usuário possui seu próprio BD de notas e produtos, por exemplo:
    'notas_fiscais_<username>.db'
    """

    def __init__(self, user_db="users.db"):
        self.user_db = user_db
        self._create_users_table()

    def _connect(self):
        return sqlite3.connect(self.user_db)

    def _create_users_table(self):
        conn = self._connect()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                date_created TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def get_user_db_path(self, username):
        """
        Retorna o caminho do BD individual do usuário.
        """
        # Ex: 'notas_fiscais_<username>.db'
        return f"notas_fiscais_{username}.db"

    def register_user(self, username, password, common_password):
        """
        Cria um usuário no 'users.db', validando a senha comum.
        Em seguida, cria o arquivo individual do usuário (notas_fiscais_<username>.db).
        """
        if common_password != "paralelo2025":
            raise Exception("Senha comum incorreta. Registro não permitido.")

        conn = self._connect()
        c = conn.cursor()

        # Verifica se usuário já existe
        c.execute("SELECT id FROM users WHERE username = ?", (username,))
        row = c.fetchone()
        if row:
            conn.close()
            raise Exception("Este nome de usuário já está em uso. Escolha outro.")

        data_criacao = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        c.execute("""
            INSERT INTO users (username, password, date_created)
            VALUES (?, ?, ?)
        """, (username, password, data_criacao))
        conn.commit()
        conn.close()

        # Cria o arquivo de BD do usuário e as tabelas (notas, produtos)
        user_db_path = self.get_user_db_path(username)
        NotaFiscalDB(user_db_path)  # apenas instanciar para criar as tabelas

    def login_user(self, username, password):
        """
        Se login der certo, retorna True; senão False.
        """
        conn = self._connect()
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username = ? AND password = ?", (username, password))
        row = c.fetchone()
        conn.close()
        return True if row else False

    def delete_user(self, username):
        """
        Remove o usuário do 'users.db' e deleta o arquivo individual de notas.
        """
        # 1) Apagar do users.db
        conn = self._connect()
        c = conn.cursor()
        c.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        conn.close()

        # 2) Remover arquivo .db do usuário
        user_db_path = self.get_user_db_path(username)
        if os.path.exists(user_db_path):
            os.remove(user_db_path)


# =============================================================================
# CLASSE DE BANCO DE DADOS DE NOTAS (INDIVIDUAL POR USUÁRIO)
# =============================================================================
class NotaFiscalDB:
    """
    Cada instância representa o BD de um usuário específico, ex: 'notas_fiscais_<username>.db'.
    Não há mais coluna de user_id, pois cada BD pertence a um único usuário.
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self._create_tables()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _create_tables(self):
        conn = self._connect()
        cursor = conn.cursor()

        # Tabela de notas
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cnpj TEXT,
                emissao TEXT,
                dados_nota TEXT
            )
        ''')

        # Tabela de produtos
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cnpj_emissao TEXT,
                produto_id TEXT,
                nome TEXT,
                categoria TEXT,
                quantidade TEXT,
                unidade TEXT,
                valor_unitario TEXT,
                valor_total TEXT
            )
        ''')

        conn.commit()
        conn.close()

    # ----------------- MÉTODOS ORIGINAIS, SEM user_id -----------------
    def listar_notas(self):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT id, cnpj, emissao FROM notas")
        notas = cursor.fetchall()
        conn.close()
        return notas

    def buscar_nota_por_id(self, nota_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM notas WHERE id = ?", (nota_id,))
        nota = cursor.fetchone()
        if nota:
            cnpj_emissao = f"{nota[1]}_{nota[2]}"
            cursor.execute("SELECT * FROM produtos WHERE cnpj_emissao = ?", (cnpj_emissao,))
            produtos = cursor.fetchall()
            conn.close()
            return nota, produtos
        conn.close()
        return None, []

    def salvar_dados(self, cnpj, emissao, dados_nota, produtos):
        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO notas (cnpj, emissao, dados_nota)
            VALUES (?, ?, ?)
        ''', (cnpj, emissao, json.dumps(dados_nota, ensure_ascii=False)))

        cnpj_emissao = f"{cnpj}_{emissao}"
        for produto in produtos:
            cursor.execute('''
                INSERT INTO produtos (
                    cnpj_emissao, produto_id, nome, categoria, quantidade, unidade,
                    valor_unitario, valor_total
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                cnpj_emissao,
                produto.get("Id"),
                produto.get("Text"),
                produto.get("Category"),
                produto.get("Traits", {}).get("Quantidade"),
                produto.get("Traits", {}).get("Unidade"),
                produto.get("Traits", {}).get("Valor Unitário"),
                produto.get("Traits", {}).get("Valor Total")
            ))
        conn.commit()
        conn.close()

    def calcular_financeiro(self, nota_ids=None):
        conn = self._connect()
        cursor = conn.cursor()

        if nota_ids:
            placeholders = ",".join("?" for _ in nota_ids)
            # categorias
            cursor.execute(f"""
                SELECT categoria, SUM(CAST(valor_total AS REAL)) AS total_gasto
                FROM produtos
                WHERE cnpj_emissao IN (
                    SELECT cnpj || '_' || emissao 
                    FROM notas
                    WHERE id IN ({placeholders})
                )
                GROUP BY categoria
            """, nota_ids)
            categorias = cursor.fetchall()

            # top 10
            cursor.execute(f"""
                SELECT nome, CAST(valor_total AS REAL) AS total_valor
                FROM produtos
                WHERE cnpj_emissao IN (
                    SELECT cnpj || '_' || emissao
                    FROM notas
                    WHERE id IN ({placeholders})
                )
                ORDER BY total_valor DESC
                LIMIT 10
            """, nota_ids)
            itens_mais_caros = cursor.fetchall()

            # total
            cursor.execute(f"""
                SELECT SUM(CAST(valor_total AS REAL))
                FROM produtos
                WHERE cnpj_emissao IN (
                    SELECT cnpj || '_' || emissao
                    FROM notas
                    WHERE id IN ({placeholders})
                )
            """, nota_ids)
            total_valor = cursor.fetchone()[0]

        else:
            # Todas as notas
            cursor.execute("""
                SELECT categoria, SUM(CAST(valor_total AS REAL)) AS total_gasto
                FROM produtos
                GROUP BY categoria
            """)
            categorias = cursor.fetchall()

            cursor.execute("""
                SELECT nome, CAST(valor_total AS REAL) AS total_valor
                FROM produtos
                ORDER BY total_valor DESC
                LIMIT 10
            """)
            itens_mais_caros = cursor.fetchall()

            cursor.execute("SELECT SUM(CAST(valor_total AS REAL)) FROM produtos")
            total_valor = cursor.fetchone()[0]

        conn.close()
        return {
            "categorias": categorias,
            "itens_mais_caros": itens_mais_caros,
            "total_valor": total_valor if total_valor else 0
        }


# =============================================================================
# FUNÇÕES AUXILIARES DE EXTRAÇÃO (LangChain)
# =============================================================================
def fetch_webpage(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        if not response.text.strip():
            raise Exception("A URL não retornou nenhum conteúdo HTML.")
        return response.text
    except requests.exceptions.RequestException as e:
        raise Exception(f"Erro ao acessar a página: {e}")

def process_html_with_langchain(html_content):
    prompt = ChatPromptTemplate.from_template("""
        Você é um modelo que analisa notas fiscais. Extraia as seguintes informações gerais da nota:
        - CNPJ do Emitente
        - Número
        - Série
        - Emissão (data)
        - Horário

        Além disso, extraia os produtos listados e organize-os no seguinte formato:
        {{
            "Dados Nota": {{
                "CNPJ": "CNPJ do Emitente",
                "Número": "Número da Nota",
                "Série": "Série da Nota",
                "Emissão": "Data de Emissão",
                "Horário": "Horário de Emissão"
            }},
            "Produtos": [
                {{
                    "Id": "Número identificador",
                    "Text": "Nome do Produto",
                    "Category": "Categoria do Produto",
                    "Traits": {{
                        "Quantidade": "Quantidade do Produto",
                        "Unidade": "Unidade de Medida",
                        "Valor Unitário": "Valor Unitário do Produto",
                        "Valor Total": "Valor Total do Produto"
                    }}
                }}
            ]
        }}
        Certifique-se de que o JSON esteja bem formatado e sem erros.

        HTML da Nota Fiscal:
        {html_content}
    """)
    llm = ChatOpenAI(model="gpt-4", temperature=0)
    runnable = RunnableMap({"entities": prompt | llm})
    try:
        result = runnable.invoke({"html_content": html_content})
        return result["entities"].content.strip()
    except Exception as e:
        raise Exception(f"Erro ao processar o HTML com o modelo: {e}")

def filtrar_dados(resultado):
    try:
        dados_extracao = json.loads(resultado)
        dados_nota = dados_extracao.get("Dados Nota", {})
        produtos = dados_extracao.get("Produtos", [])
        return {
            "CNPJ": dados_nota.get("CNPJ", "Não informado"),
            "Emissao": dados_nota.get("Emissão", "Não informado"),
            "Dados Nota": dados_nota,
            "Produtos": produtos
        }
    except json.JSONDecodeError as e:
        raise Exception("Erro ao decodificar JSON", e)


# =============================================================================
# FUNÇÕES PARA A LÓGICA DO SISTEMA
# =============================================================================
# Instância global do gerenciador de usuários
user_manager = UserManager()


def registrar_conta(username, password, common_password, state):
    try:
        user_manager.register_user(username, password, common_password)
        state["logged_in"] = True
        state["username"] = username
        return f"Conta criada! Usuário: {username}"
    except Exception as e:
        return f"Erro ao registrar: {str(e)}"


def login_conta(username, password, state):
    ok = user_manager.login_user(username, password)
    if ok:
        state["logged_in"] = True
        state["username"] = username
        return f"Login bem-sucedido! Usuário: {username}"
    else:
        return "Login inválido. Verifique usuário e senha."


def logout_conta(state):
    if not state["logged_in"]:
        return "Você já está deslogado."
    name = state.get("username", "")
    state["logged_in"] = False
    state["username"] = ""
    return f"Logout efetuado. Até mais, {name}!"


def excluir_conta(state):
    if not state["logged_in"]:
        return "Você não está logado."
    name = state["username"]
    user_manager.delete_user(name)
    state["logged_in"] = False
    state["username"] = ""
    return f"Conta de {name} excluída com sucesso!"


# =============================================================================
# FUNÇÕES RELACIONADAS AO BD DE CADA USUÁRIO
# =============================================================================
def get_user_db(state):
    """
    Devolve uma instância NotaFiscalDB com base no username logado.
    """
    username = state["username"]
    db_path = user_manager.get_user_db_path(username)
    return NotaFiscalDB(db_path)


def adicionar_nota(url, state):
    if not state["logged_in"]:
        return "Você não está logado. Faça login para adicionar notas."

    try:
        html_content = fetch_webpage(url)
        resultado = process_html_with_langchain(html_content)
        dados_filtrados = filtrar_dados(resultado)

        db = get_user_db(state)
        db.salvar_dados(
            dados_filtrados["CNPJ"],
            dados_filtrados["Emissao"],
            dados_filtrados["Dados Nota"],
            dados_filtrados["Produtos"]
        )
        return "Nota adicionada com sucesso!"
    except Exception as e:
        return f"Erro ao adicionar a nota: {e}"


def buscar_detalhes_por_id(nota_id, state):
    if not state["logged_in"]:
        return "Você não está logado."
    db = get_user_db(state)
    nota, produtos = db.buscar_nota_por_id(nota_id)
    if nota:
        detalhes = f"Nota Fiscal:\nID: {nota[0]}, CNPJ: {nota[1]}, Emissão: {nota[2]}\n\nProdutos:\n"
        for p in produtos:
            detalhes += (
                f"  - Nome: {p[3]}, Categoria: {p[4]}, "
                f"Quantidade: {p[5]}, Unidade: {p[6]}, "
                f"Valor Unit.: {p[7]}, Valor Total: {p[8]}\n"
            )
        return detalhes
    else:
        return "Nota não encontrada."


def listar_notas(state):
    if not state["logged_in"]:
        return "Você não está logado."
    db = get_user_db(state)
    rows = db.listar_notas()
    if not rows:
        return "Nenhuma nota cadastrada."
    return "\n".join([
        f"ID: {r[0]}, CNPJ: {r[1]}, Emissão: {r[2]}" for r in rows
    ])


def calcular_financeiro_interface(notas_selecionadas, state):
    if not state["logged_in"]:
        return "Você não está logado."

    db = get_user_db(state)

    if notas_selecionadas.strip():
        ids = [int(x.strip()) for x in notas_selecionadas.split(",") if x.strip().isdigit()]
    else:
        ids = None

    result = db.calcular_financeiro(ids)
    cat_txt = "\n".join([f"{c[0]}: R$ {c[1]:.2f}" for c in result["categorias"]])
    caros_txt = "\n".join([f"{i[0]}: R$ {i[1]:.2f}" for i in result["itens_mais_caros"]])
    total_txt = f"R$ {result['total_valor']:.2f}"
    return (
        f"Categorias mais compradas:\n{cat_txt}\n\n"
        f"Top 10 itens mais caros:\n{caros_txt}\n\n"
        f"Valor total: {total_txt}"
    )


def gerar_consultoria(state):
    if not state["logged_in"]:
        return "Você não está logado."

    db = get_user_db(state)
    conn = db._connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT nome, cnpj_emissao, valor_unitario
        FROM produtos
        WHERE nome != ''
          AND valor_unitario != ''
        ORDER BY nome
    """)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "Nenhum produto encontrado para consultoria."

    resumo = "Lista de Produtos, CNPJ e Preço Unitário:\n"
    for nome, cnpj, valor in rows:
        resumo += f"- Produto: {nome}; Mercado/Emissor: {cnpj}; Preço Unit.: {valor}\n"

    consultoria_prompt = ChatPromptTemplate.from_template("""
        Você é um consultor que avalia variações de preços em supermercados.
        Receba a lista de produtos abaixo (com CNPJ do mercado + preço unitário)
        e forneça:

        1. Comparação de valores (onde está mais barato ou mais caro).
        2. Observações sobre variações de preço.
        3. Dicas de consumo.

        Lista de produtos:
        {resumo}

        Escreva em português claro e objetivo.
    """)
    llm = ChatOpenAI(model="gpt-4", temperature=0)
    runnable = RunnableMap({"analysis": consultoria_prompt | llm})

    try:
        result = runnable.invoke({"resumo": resumo})
        return result["analysis"].content.strip()
    except Exception as e:
        return f"Erro ao gerar consultoria: {str(e)}"


# =============================================================================
# INTERFACE GRADIO
# =============================================================================
with gr.Blocks() as interface:
    # Armazena se o usuário está logado, etc.
    state = gr.State({"logged_in": False, "username": ""})

    gr.Markdown("## Eagle 0.1 - Bancos de Dados Separados por Usuário")

    # Mostra no topo o nome do usuário logado
    def label_usuario(st):
        if st["logged_in"]:
            return f"Usuário logado: {st['username']}"
        else:
            return "Usuário logado: (desconectado)"

    usuario_label = gr.Markdown(label_usuario(state.value))

    # -- ABA REGISTRAR --
    with gr.Tab("Registrar"):
        username_reg = gr.Textbox(label="Usuário")
        password_reg = gr.Textbox(label="Senha", type="password")
        common_reg = gr.Textbox(label="Senha Comum", type="password")
        registrar_btn = gr.Button("Registrar")
        registrar_out = gr.Textbox(label="Status do Registro", lines=2)

        def acao_registrar(u, p, c, st):
            msg = registrar_conta(u, p, c, st)
            lbl = label_usuario(st)
            return (msg, lbl, st)

        registrar_btn.click(
            fn=acao_registrar,
            inputs=[username_reg, password_reg, common_reg, state],
            outputs=[registrar_out, usuario_label, state]
        )

    # -- ABA LOGIN --
    with gr.Tab("Login"):
        username_login = gr.Textbox(label="Usuário")
        password_login = gr.Textbox(label="Senha", type="password")
        login_btn = gr.Button("Login")
        login_out = gr.Textbox(label="Status do Login", lines=2)

        def acao_login(u, p, st):
            msg = login_conta(u, p, st)
            lbl = label_usuario(st)
            return (msg, lbl, st)

        login_btn.click(
            fn=acao_login,
            inputs=[username_login, password_login, state],
            outputs=[login_out, usuario_label, state]
        )

    # -- ABA CONTA (LOGOUT / EXCLUIR) --
    with gr.Tab("Conta"):
        logout_btn = gr.Button("Logout")
        logout_out = gr.Textbox(label="Status Logout", lines=1)

        excluir_btn = gr.Button("Excluir Conta")
        excluir_out = gr.Textbox(label="Status Exclusão", lines=2)

        def acao_logout(st):
            msg = logout_conta(st)
            lbl = label_usuario(st)
            return (msg, lbl, st)

        logout_btn.click(
            fn=acao_logout,
            inputs=[state],
            outputs=[logout_out, usuario_label, state]
        )

        def acao_excluir(st):
            msg = excluir_conta(st)
            lbl = label_usuario(st)
            return (msg, lbl, st)

        excluir_btn.click(
            fn=acao_excluir,
            inputs=[state],
            outputs=[excluir_out, usuario_label, state]
        )

    # -- ABA LISTAR NOTAS --
    with gr.Tab("Listar Notas"):
        listar_btn = gr.Button("Listar Notas")
        notas_output = gr.Textbox(label="Notas Fiscais")

        def acao_listar(st):
            return listar_notas(st)

        listar_btn.click(
            fn=acao_listar,
            inputs=[state],
            outputs=notas_output
        )

    # -- ABA ADICIONAR NOTA --
    with gr.Tab("Adicionar Nota"):
        url_input = gr.Textbox(label="URL da NFC-e")
        adicionar_btn = gr.Button("Adicionar Nota")
        adicionar_output = gr.Textbox(label="Status")

        def acao_adicionar(url, st):
            return adicionar_nota(url, st)

        adicionar_btn.click(
            fn=acao_adicionar,
            inputs=[url_input, state],
            outputs=adicionar_output
        )

    # -- ABA BUSCAR NOTA POR ID --
    with gr.Tab("Buscar Nota por ID"):
        nota_id_input = gr.Textbox(label="ID da Nota Fiscal")
        buscar_id_btn = gr.Button("Buscar")
        nota_id_output = gr.Textbox(label="Detalhes da Nota Fiscal")

        def acao_buscar(nid, st):
            return buscar_detalhes_por_id(nid, st)

        buscar_id_btn.click(
            fn=acao_buscar,
            inputs=[nota_id_input, state],
            outputs=nota_id_output
        )

    # -- ABA ÁREA FINANCEIRA --
    with gr.Tab("Área Financeira"):
        notas_selecionadas = gr.Textbox(label="IDs das Notas (ex: 1,2) ou vazio p/ todas")
        calcular_btn = gr.Button("Calcular")
        financeiro_output = gr.Textbox(label="Resultados Financeiros")

        def acao_financeiro(ids, st):
            return calcular_financeiro_interface(ids, st)

        calcular_btn.click(
            fn=acao_financeiro,
            inputs=[notas_selecionadas, state],
            outputs=financeiro_output
        )

    # -- ABA CONSULTORIA --
    with gr.Tab("Consultoria"):
        gr.Markdown("### Análise de variações de preços e dicas de consumo")
        consultoria_btn = gr.Button("Gerar Consultoria")
        consultoria_output = gr.Textbox(label="Relatório de Consultoria", lines=15)

        def acao_consulta(st):
            return gerar_consultoria(st)

        consultoria_btn.click(
            fn=acao_consulta,
            inputs=[state],
            outputs=consultoria_output
        )

    interface.launch()
