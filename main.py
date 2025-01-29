import gradio as gr
from datetime import datetime
import sqlite3
import requests
import json
import os
from langchain.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain.schema.runnable import RunnableMap
from dotenv import load_dotenv
# Carregar variáveis do arquivo .env
load_dotenv()

# Acessar a chave da OpenAI
openai_api_key = os.getenv("OPENAI_API_KEY")

if not openai_api_key:
    raise ValueError("A chave da OpenAI não foi encontrada no arquivo .env")

# Configurar a variável de ambiente
os.environ["OPENAI_API_KEY"] = openai_api_key
# =============================================================================
# BANCO DE DADOS
# =============================================================================
class NotaFiscalDB:
    def __init__(self, db_name="notas_fiscais.db"):
        self.db_name = db_name
        self.criar_banco()

    def conectar(self):
        return sqlite3.connect(self.db_name)

    def criar_banco(self):
        """
        Cria as tabelas originais + estrutura para usuários (login).
        """
        conn = self.conectar()
        cursor = conn.cursor()

        # Tabela original: notas
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cnpj TEXT,
                emissao TEXT,
                dados_nota TEXT
            )
        ''')

        # Tabela original: produtos
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

        # NOVO: Tabela de usuários
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                date_created TEXT NOT NULL
            )
        ''')

        # NOVO: Adiciona user_id a notas e produtos (se não existir)
        try:
            cursor.execute("ALTER TABLE notas ADD COLUMN user_id INTEGER")
        except:
            pass

        try:
            cursor.execute("ALTER TABLE produtos ADD COLUMN user_id INTEGER")
        except:
            pass

        # CHAVE IMPORTANTE: user_note_id (ID local da nota por usuário)
        try:
            cursor.execute("ALTER TABLE notas ADD COLUMN user_note_id INTEGER")
        except:
            pass

        conn.commit()
        conn.close()

    # ----------------- MÉTODOS DE USUÁRIO -----------------
    def register_user(self, username, password, common_password):
        """
        Registra um novo usuário, exigindo a senha comum 'paralelo2025'.
        Retorna o ID do usuário criado.
        """
        if common_password != "paralelo2025":
            raise Exception("Senha comum incorreta. Registro não permitido.")

        conn = self.conectar()
        cursor = conn.cursor()

        # Verifica se usuário já existe
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        if row:
            conn.close()
            raise Exception("Este nome de usuário já está em uso. Escolha outro.")

        data_criacao = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            INSERT INTO users (username, password, date_created)
            VALUES (?, ?, ?)
        ''', (username, password, data_criacao))
        conn.commit()

        new_id = cursor.lastrowid
        conn.close()
        return new_id

    def login_user(self, username, password):
        """
        Se login der certo, retorna user_id; senão None.
        """
        conn = self.conectar()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ? AND password = ?", (username, password))
        row = cursor.fetchone()
        conn.close()
        if row:
            return row[0]  # user_id
        else:
            return None

    def delete_user(self, user_id):
        """
        Remove o usuário e todas as notas/produtos dele.
        """
        conn = self.conectar()
        cursor = conn.cursor()

        # Apaga produtos e notas do user
        cursor.execute("DELETE FROM produtos WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM notas WHERE user_id = ?", (user_id,))

        # Apaga o user
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()

    # ----------------- MÉTODOS ORIGINAIS (ADAPTADOS) --------------
    def listar_notas(self, user_id=None):
        """
        Em vez de exibir o 'id' autoincrement do BD, exibimos 'user_note_id'.
        """
        conn = self.conectar()
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute("SELECT user_note_id, cnpj, emissao FROM notas WHERE user_id = ?", (user_id,))
        else:
            # Modo antigo, sem user
            cursor.execute("SELECT user_note_id, cnpj, emissao FROM notas")
        notas = cursor.fetchall()
        conn.close()
        return notas

    def buscar_nota_por_id(self, user_note_id, user_id=None):
        """
        Agora a busca é feita por user_note_id (ID local do usuário), e não pelo 'id' global.
        """
        conn = self.conectar()
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute("SELECT * FROM notas WHERE user_note_id = ? AND user_id = ?", (user_note_id, user_id))
        else:
            cursor.execute("SELECT * FROM notas WHERE user_note_id = ?", (user_note_id,))
        nota = cursor.fetchone()
        if nota:
            cnpj_emissao = f"{nota[1]}_{nota[2]}"
            if user_id is not None:
                cursor.execute("SELECT * FROM produtos WHERE cnpj_emissao = ? AND user_id = ?", (cnpj_emissao, user_id))
            else:
                cursor.execute("SELECT * FROM produtos WHERE cnpj_emissao = ?", (cnpj_emissao,))
            produtos = cursor.fetchall()
            conn.close()
            return nota, produtos
        conn.close()
        return None, []

    def salvar_dados(self, cnpj, emissao, dados_nota, produtos, user_id=None):
        """
        Salva nota e produtos no BD. Gera 'user_note_id' incremental por usuário.
        """
        conn = self.conectar()
        cursor = conn.cursor()

        # Pegar o maior user_note_id já existente para este user_id e incrementar
        if user_id is not None:
            cursor.execute("""
                SELECT COALESCE(MAX(user_note_id), 0)
                FROM notas
                WHERE user_id = ?
            """, (user_id,))
            max_note_for_user = cursor.fetchone()[0]
            new_user_note_id = max_note_for_user + 1
        else:
            new_user_note_id = None  # Modo antigo: sem user

        cursor.execute('''
            INSERT INTO notas (cnpj, emissao, dados_nota, user_id, user_note_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            cnpj,
            emissao,
            json.dumps(dados_nota, ensure_ascii=False),
            user_id,
            new_user_note_id
        ))

        cnpj_emissao = f"{cnpj}_{emissao}"
        nota_pk_id = cursor.lastrowid  # PK global, se quiser

        for produto in produtos:
            cursor.execute('''
                INSERT INTO produtos (
                    cnpj_emissao, produto_id, nome, categoria, quantidade, unidade,
                    valor_unitario, valor_total, user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                cnpj_emissao,
                produto.get("Id"),
                produto.get("Text"),
                produto.get("Category"),
                produto.get("Traits", {}).get("Quantidade"),
                produto.get("Traits", {}).get("Unidade"),
                produto.get("Traits", {}).get("Valor Unitário"),
                produto.get("Traits", {}).get("Valor Total"),
                user_id
            ))
        conn.commit()
        conn.close()

    def calcular_financeiro(self, nota_ids=None, user_id=None):
        """
        'nota_ids' agora são user_note_ids, não o autoincrement global.
        """
        conn = self.conectar()
        cursor = conn.cursor()

        if user_id:
            if nota_ids:
                placeholders = ",".join("?" for _ in nota_ids)
                # Precisamos converter user_note_id -> cnpj_emissao
                # via subselect:
                cursor.execute(f"""
                    SELECT categoria, SUM(CAST(valor_total AS REAL)) AS total_gasto
                    FROM produtos
                    WHERE user_id = ?
                      AND cnpj_emissao IN (
                        SELECT cnpj || '_' || emissao 
                        FROM notas
                        WHERE user_id = ?
                          AND user_note_id IN ({placeholders})
                      )
                    GROUP BY categoria
                """, [user_id, user_id, *nota_ids])
                categorias = cursor.fetchall()

                cursor.execute(f"""
                    SELECT nome, CAST(valor_total AS REAL) AS total_valor
                    FROM produtos
                    WHERE user_id = ?
                      AND cnpj_emissao IN (
                        SELECT cnpj || '_' || emissao 
                        FROM notas
                        WHERE user_id = ?
                          AND user_note_id IN ({placeholders})
                      )
                    ORDER BY total_valor DESC
                    LIMIT 10
                """, [user_id, user_id, *nota_ids])
                itens_mais_caros = cursor.fetchall()

                cursor.execute(f"""
                    SELECT SUM(CAST(valor_total AS REAL))
                    FROM produtos
                    WHERE user_id = ?
                      AND cnpj_emissao IN (
                        SELECT cnpj || '_' || emissao 
                        FROM notas
                        WHERE user_id = ?
                          AND user_note_id IN ({placeholders})
                      )
                """, [user_id, user_id, *nota_ids])
                total_valor = cursor.fetchone()[0]
            else:
                # Todas do user
                cursor.execute("""
                    SELECT categoria, SUM(CAST(valor_total AS REAL)) AS total_gasto
                    FROM produtos
                    WHERE user_id = ?
                    GROUP BY categoria
                """, (user_id,))
                categorias = cursor.fetchall()

                cursor.execute("""
                    SELECT nome, CAST(valor_total AS REAL) AS total_valor
                    FROM produtos
                    WHERE user_id = ?
                    ORDER BY total_valor DESC
                    LIMIT 10
                """, (user_id,))
                itens_mais_caros = cursor.fetchall()

                cursor.execute("""
                    SELECT SUM(CAST(valor_total AS REAL))
                    FROM produtos
                    WHERE user_id = ?
                """, (user_id,))
                total_valor = cursor.fetchone()[0]
        else:
            # Modo antigo (sem user)
            if nota_ids:
                placeholders = ",".join("?" for _ in nota_ids)
                cursor.execute(f"""
                    SELECT categoria, SUM(CAST(valor_total AS REAL)) AS total_gasto
                    FROM produtos
                    WHERE cnpj_emissao IN (
                        SELECT cnpj || '_' || emissao 
                        FROM notas
                        WHERE user_note_id IN ({placeholders})
                    )
                    GROUP BY categoria
                """, nota_ids)
                categorias = cursor.fetchall()

                cursor.execute(f"""
                    SELECT nome, CAST(valor_total AS REAL) AS total_valor
                    FROM produtos
                    WHERE cnpj_emissao IN (
                        SELECT cnpj || '_' || emissao
                        FROM notas
                        WHERE user_note_id IN ({placeholders})
                    )
                    ORDER BY total_valor DESC
                    LIMIT 10
                """, nota_ids)
                itens_mais_caros = cursor.fetchall()

                cursor.execute(f"""
                    SELECT SUM(CAST(valor_total AS REAL))
                    FROM produtos
                    WHERE cnpj_emissao IN (
                        SELECT cnpj || '_' || emissao
                        FROM notas
                        WHERE user_note_id IN ({placeholders})
                    )
                """, nota_ids)
                total_valor = cursor.fetchone()[0]
            else:
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
# FUNÇÕES AUXILIARES
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
        IMPORTANTE: não escreva nada além do JSON.

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

        HTML da Nota Fiscal:
        {html_content}
    """)
    llm = ChatOpenAI(model="gpt-4", temperature=0)
    runnable = RunnableMap({"entities": prompt | llm})
    try:
        result = runnable.invoke({"html_content": html_content})
        resposta = result["entities"].content.strip()

        # Checagem simples se começa com { ou [
        if not (resposta.startswith("{") or resposta.startswith("[")):
            raise Exception(f"Retorno do LLM não parece JSON:\n{resposta[:200]}")

        return resposta
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
# FUNÇÕES DO SISTEMA (LOGIN, REGISTRO, ETC.)
# =============================================================================
def registrar_conta(username, password, common_password, state):
    db = NotaFiscalDB()
    try:
        new_user_id = db.register_user(username, password, common_password)
        state["logged_in"] = True
        state["user_id"] = new_user_id
        state["username"] = username
        return f"Conta criada! Usuário: {username}"
    except Exception as e:
        return f"Erro ao registrar: {str(e)}"

def login_conta(username, password, state):
    db = NotaFiscalDB()
    user_id = db.login_user(username, password)
    if user_id:
        state["logged_in"] = True
        state["user_id"] = user_id
        state["username"] = username
        return f"Login bem-sucedido! Usuário: {username}"
    else:
        return "Login inválido. Verifique usuário e senha."

def logout_conta(state):
    if not state["logged_in"]:
        return "Você já está deslogado."
    name = state.get("username", "")
    state["logged_in"] = False
    state["user_id"] = None
    state["username"] = ""
    return f"Logout efetuado. Até mais, {name}!"

def excluir_conta(state):
    if not state["logged_in"]:
        return "Você não está logado."
    db = NotaFiscalDB()
    user_id = state["user_id"]
    name = state["username"]
    db.delete_user(user_id)

    state["logged_in"] = False
    state["user_id"] = None
    state["username"] = ""
    return f"Conta de {name} excluída com sucesso!"


# =============================================================================
# FUNÇÕES DE NOTAS
# =============================================================================
def adicionar_nota(url, state):
    if not state["logged_in"]:
        return "Você não está logado. Faça login para adicionar notas."

    try:
        html_content = fetch_webpage(url)
        resultado = process_html_with_langchain(html_content)
        dados_filtrados = filtrar_dados(resultado)
        db = NotaFiscalDB()
        db.salvar_dados(
            dados_filtrados["CNPJ"],
            dados_filtrados["Emissao"],
            dados_filtrados["Dados Nota"],
            dados_filtrados["Produtos"],
            user_id=state["user_id"]  # vinculado ao dono
        )
        return "Nota adicionada com sucesso!"
    except Exception as e:
        return f"Erro ao adicionar a nota: {e}"

def buscar_detalhes_por_id(nota_id, state):
    """
    nota_id agora significa user_note_id (ID local).
    """
    if not state["logged_in"]:
        return "Você não está logado."
    db = NotaFiscalDB()
    nota, produtos = db.buscar_nota_por_id(nota_id, user_id=state["user_id"])
    if nota:
        # Lembre: nota[0] = 'id'(pk), nota[1] = cnpj, nota[2] = emissao, nota[3] = dados_nota,
        #         nota[4] = user_id, nota[5] = user_note_id
        detalhes = (
            f"Nota Fiscal:\n"
            f"ID: {nota[5]}, CNPJ: {nota[1]}, Emissão: {nota[2]}\n\nProdutos:\n"
        )
        for p in produtos:
            detalhes += (
                f"  - Nome: {p[3]}, Categoria: {p[4]}, "
                f"Quantidade: {p[5]}, Unidade: {p[6]}, "
                f"Valor Unit.: {p[7]}, Valor Total: {p[8]}\n"
            )
        return detalhes
    else:
        return "Nota não encontrada ou não pertence a você."

def listar_notas(state):
    if not state["logged_in"]:
        return "Você não está logado."
    db = NotaFiscalDB()
    rows = db.listar_notas(user_id=state["user_id"])
    if not rows:
        return "Nenhuma nota cadastrada."
    # rows => [(user_note_id, cnpj, emissao), ...]
    return "\n".join([
        f"ID: {r[0]}, CNPJ: {r[1]}, Emissão: {r[2]}" for r in rows
    ])

def calcular_financeiro_interface(notas_selecionadas, state):
    """
    'notas_selecionadas' são os user_note_ids que o usuário digitou.
    """
    if not state["logged_in"]:
        return "Você não está logado."
    db = NotaFiscalDB()

    if notas_selecionadas.strip():
        ids = [int(x.strip()) for x in notas_selecionadas.split(",") if x.strip().isdigit()]
    else:
        ids = None

    result = db.calcular_financeiro(ids, user_id=state["user_id"])
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
    db = NotaFiscalDB()
    conn = db.conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT nome, cnpj_emissao, valor_unitario
        FROM produtos
        WHERE user_id = ?
          AND nome != ''
          AND valor_unitario != ''
        ORDER BY nome
    """, (state["user_id"],))
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
    state = gr.State({"logged_in": False, "user_id": None, "username": ""})

    gr.Markdown("## Eagle 0.1")

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
        nota_id_input = gr.Textbox(label="ID da Nota Fiscal (Local)")
        buscar_id_btn = gr.Button("Buscar")
        nota_id_output = gr.Textbox(label="Detalhes da Nota Fiscal")

        def acao_buscar(nid, st):
            # lembre-se: aqui 'nid' é user_note_id
            return buscar_detalhes_por_id(nid, st)

        buscar_id_btn.click(
            fn=acao_buscar,
            inputs=[nota_id_input, state],
            outputs=nota_id_output
        )

    # -- ABA ÁREA FINANCEIRA --
    with gr.Tab("Área Financeira"):
        notas_selecionadas = gr.Textbox(label="IDs das Notas (ex: 1,2) ou vazio p/ todas (local)")
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
