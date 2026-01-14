from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from werkzeug.utils import secure_filename
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import re
from datetime import datetime
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv  # <-- NOVA LINHA

load_dotenv() # <-- NOVA LINHA (Carrega as senhas do .env)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'xando_projeto_imobiliaria') # Busca do .env

# --- CONFIGURAÇÃO DE UPLOAD ---
UPLOAD_FOLDER = 'static/uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- CONEXÃO COM O BANCO (SUPABASE) --- ***
def get_db_connection():
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASS'),
        port=os.getenv('DB_PORT')
    )

# --- UTILITÁRIOS ---
def tem_permissao(chave_permissao):
    if 'permissoes' not in session:
        return False
    return chave_permissao in session['permissoes']

def validar_documento(doc):
    doc = re.sub(r'\D', '', doc)
    if len(doc) == 11:
        if doc == doc[0] * 11: return False
        for i in range(9, 11):
            val = sum((int(doc[num]) * ((i + 1) - num) for num in range(i)))
            digit = ((val * 10) % 11) % 10
            if digit != int(doc[i]): return False
        return True
    elif len(doc) == 14:
        for i in [12, 13]:
            peso = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2] if i == 12 else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
            val = sum(int(doc[n]) * peso[n] for n in range(i))
            digit = 0 if val % 11 < 2 else 11 - (val % 11)
            if digit != int(doc[i]): return False
        return True
    return False

# --- ROTAS DE ACESSO ---

@app.route('/')
def index():
    if 'usuario_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_input = request.form.get('login')
        senha_input = request.form.get('senha')
        
        conn = None
        cur = None
        
        try:
            conn = get_db_connection()
            # Usamos RealDictCursor para acessar os dados pelo nome da coluna
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # 1. Busca o usuário e os dados básicos do colaborador
            cur.execute("""
                SELECT u.id, c.nome, u.nivel_acesso, u.perfil_id
                FROM usuarios u
                JOIN colaboradores c ON u.colaborador_id = c.id
                WHERE u.login = %s AND u.senha = %s
            """, (login_input, senha_input))
            user = cur.fetchone()
            
            if user:
                session['usuario_id'] = user['id']
                session['nome_usuario'] = user['nome']
                session['user_nivel'] = user['nivel_acesso']
                
                try:
                    # Busca as permissões reais no banco
                    cur.execute("""
                        SELECT p.chave FROM permissoes p
                        JOIN perfil_permissoes pp ON p.id = pp.permissao_id
                        WHERE pp.perfil_id = %s
                    """, (user['perfil_id'],))
                    permissoes = [row['chave'] for row in cur.fetchall()]
                    
                    # Se for ADMIN e a lista vier vazia, forçamos a permissão para o dashboard não travar
                    if user['nivel_acesso'] == 'ADMIN' and not permissoes:
                        permissoes = ['admin_usuarios']
                        
                    session['permissoes'] = permissoes
                except:
                    # Se a tabela de permissões nem existir ainda, damos acesso para teste
                    session['permissoes'] = ['admin_usuarios']

                return redirect(url_for('dashboard'))
            # Se não encontrar usuário, volta com mensagem de erro
            return render_template('login.html', erro="Usuário ou senha incorretos!")
            
        except Exception as e:
            # Em caso de erro no banco, exibe na tela para facilitar o seu debug
            return f"Erro técnico no banco de dados: {e}"
        finally:
            if cur: cur.close()
            if conn: conn.close()
            
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    return render_template('dashboard.html', nome=session['nome_usuario'],
                            user_nome=session['nome_usuario'], user_nivel=session['user_nivel'])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- PAINEL ADMINISTRATIVO ---
@app.route('/painel_adm')
def painel_adm():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    
    page = int(request.args.get('page', 0))
    offset = page * 5
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT COUNT(*) as total FROM imoveis")
        total_imoveis = cur.fetchone()['total']
        cur.execute("SELECT SUM(valor_captacao) as valor_total FROM imoveis")
        res_v = cur.fetchone()
        valor_estoque = res_v['valor_total'] if res_v['valor_total'] else 0
        
        cur.execute("SELECT tipo, COUNT(*) as quantidade FROM imoveis GROUP BY tipo")
        dados_grafico = cur.fetchall()
        labels = [d['tipo'] for d in dados_grafico]
        valores = [d['quantidade'] for d in dados_grafico]
        
        cur.execute('''
            SELECT i.id, i.cod_elemento, i.tipo, i.valor_captacao, c.nome as captador, s.nome as status_nome
            FROM imoveis i
            JOIN usuarios u ON i.captador_id = u.id
            JOIN colaboradores c ON u.colaborador_id = c.id
            LEFT JOIN status_imovel s ON i.status_id = s.id
            ORDER BY i.id DESC LIMIT 6 OFFSET %s
        ''', (offset,))
        captacoes = cur.fetchall()
        tem_mais = len(captacoes) > 5
        
        return render_template('adm.html', total=total_imoveis, valor=valor_estoque, labels=labels, valores=valores, captacoes=captacoes[:5], page=page, tem_mais=tem_mais)
    finally:
        cur.close()
        conn.close()

# --- CLIENTES ---
@app.route('/verificar_documento', methods=['POST'])
def verificar_documento():
    data = request.json
    doc_original = data.get('documento', '')
    doc_limpo = re.sub(r'\D', '', doc_original)
    if not validar_documento(doc_limpo):
        return jsonify({'status': 'erro', 'mensagem': '⚠️ CPF ou CNPJ inválido.'})
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT nome_razao FROM clientes WHERE documento = %s", (doc_limpo,))
    cliente = cur.fetchone()
    cur.close()
    conn.close()
    
    if cliente:
        return jsonify({'status': 'duplicado', 'mensagem': f'⛔ Já cadastrado: {cliente["nome_razao"]}.'})
    return jsonify({'status': 'sucesso', 'mensagem': '✅ Documento livre.'})

@app.route('/salvar_cliente', methods=['POST'])
def salvar_cliente():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    d = request.form
    doc_limpo = re.sub(r'\D', '', d['documento'])
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('''
            INSERT INTO clientes (tipo_pessoa, documento, nome_razao, email, telefone, cep, endereco_completo, cadastrado_por_id, data_cadastro)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ''', (d['tipo_pessoa'], doc_limpo, d['nome_razao'], d['email'], d['telefone'], d['cep'], d['endereco_completo'], session['usuario_id']))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('lista_clientes'))

@app.route('/lista_clientes')
def lista_clientes():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    termo = request.args.get('q', '').strip()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if termo:
        termo_limpo = re.sub(r'\D', '', termo)
        cur.execute("SELECT * FROM clientes WHERE documento ILIKE %s OR nome_razao ILIKE %s ORDER BY nome_razao", (f'%{termo_limpo}%', f'%{termo}%'))
    else:
        cur.execute('SELECT * FROM clientes ORDER BY nome_razao ASC')
    clientes = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('lista_clientes.html', clientes=clientes, busca=termo)

# --- IMÓVEIS ---
@app.route('/captacao')
def captacao():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    return render_template('captacao.html')

@app.route('/salvar_imovel', methods=['POST'])
def salvar_imovel():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    d = request.form
    doc_prop = d.get('documento_proprietario')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM clientes WHERE documento = %s", (doc_prop,))
        res_prop = cur.fetchone()
        prop_id = res_prop[0] if res_prop else None
        cep_limpo = d['cep'].replace('-', '')
        
        cur.execute("SELECT COUNT(*) FROM imoveis WHERE tipo = %s AND cep LIKE %s", (d['tipo'], cep_limpo[:5] + '%'))
        total = cur.fetchone()[0]
        codigo_final = f"{d['tipo']}-{cep_limpo[:5]}-{str(total + 1).zfill(3)}"
        
        cur.execute('''
            INSERT INTO imoveis (cod_elemento, tipo, cep, valor_captacao, endereco, complemento, metragem, quartos, suites, vagas, observacoes, proprietario_id, captador_id, data_cadastro, status_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, 1)
        ''', (codigo_final, d['tipo'], cep_limpo, d['valor_imovel'] or 0, d['endereco'], d['complemento'], d['metragem'] or 0, d['quartos'] or 0, d['suites'] or 0, d['vagas'] or 0, d['observacoes'], prop_id, session['usuario_id']))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('estoque'))

@app.route('/estoque')
def estoque():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    termo = request.args.get('q', '').strip()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    query = '''
        SELECT i.*, s.nome as status_nome, c.nome_razao as nome_proprietario,
        (SELECT url FROM midias WHERE imovel_id = i.id ORDER BY id ASC LIMIT 1) as foto_capa
        FROM imoveis i
        LEFT JOIN status_imovel s ON i.status_id = s.id
        LEFT JOIN clientes c ON i.proprietario_id = c.id
        WHERE 1=1
    '''
    params = []
    if termo:
        query += " AND (i.cod_elemento ILIKE %s OR i.endereco ILIKE %s OR c.nome_razao ILIKE %s)"
        params.extend([f'%{termo}%', f'%{termo}%', f'%{termo}%'])
    cur.execute(query + " ORDER BY i.id DESC", params)
    imoveis = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('estoque.html', imoveis=imoveis, busca=termo)

@app.route('/imovel/<int:id>')
def detalhes_imovel(id):
    if 'usuario_id' not in session: return redirect(url_for('login'))
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT i.*, s.nome as status_nome, c.nome_razao as nome_proprietario, 
               c.telefone as tel_proprietario, c.email as email_proprietario
        FROM imoveis i
        LEFT JOIN status_imovel s ON i.status_id = s.id
        LEFT JOIN clientes c ON i.proprietario_id = c.id
        WHERE i.id = %s
    ''', (id,))
    imovel = cur.fetchone()
    cur.execute("SELECT id, url FROM midias WHERE imovel_id = %s", (id,))
    fotos = cur.fetchall()
    cur.execute('''
        SELECT h.data_alteracao, h.observacao, s.nome as status_nome, col.nome as usuario_nome
        FROM historico_status_imovel h
        JOIN status_imovel s ON h.status_id = s.id
        JOIN usuarios u ON h.usuario_id = u.id
        JOIN colaboradores col ON u.colaborador_id = col.id
        WHERE h.imovel_id = %s ORDER BY h.data_alteracao DESC
    ''', (id,))
    historico = cur.fetchall()
    cur.execute("SELECT * FROM status_imovel ORDER BY nome")
    lista_status = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('detalhes_imovel.html', imovel=imovel, fotos=fotos, historico=historico, lista_status=lista_status)

@app.route('/upload_fotos/<int:imovel_id>', methods=['POST'])
def upload_fotos(imovel_id):
    if 'usuario_id' not in session: return redirect(url_for('login'))
    files = request.files.getlist('fotos')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for file in files:
            if file.filename != '':
                filename = secure_filename(f"{imovel_id}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                cur.execute("INSERT INTO midias (imovel_id, url) VALUES (%s, %s)", (imovel_id, filename))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('detalhes_imovel', id=imovel_id))

# --- CONFIGURAÇÕES E EQUIPE ---
@app.route('/configuracoes')
def configuracoes():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT u.id, u.login, p.nome as perfil_nome, c.nome as colaborador_nome, 
               c.status_operacao, u.perfil_id, u.colaborador_id
        FROM usuarios u
        JOIN colaboradores c ON u.colaborador_id = c.id
        LEFT JOIN perfis p ON u.perfil_id = p.id
        ORDER BY c.nome ASC
    ''')
    usuarios = cur.fetchall()
    cur.execute("SELECT id, nome FROM colaboradores WHERE status_operacao = TRUE ORDER BY nome")
    colaboradores = cur.fetchall()
    cur.execute("SELECT id, nome FROM perfis ORDER BY nome")
    perfis = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('configuracoes.html', usuarios=usuarios, colaboradores=colaboradores, perfis=perfis)

@app.route('/salvar_usuario', methods=['POST'])
def salvar_usuario():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    d = request.form
    user_id = d.get('user_id') 
    login = d.get('login')
    senha = d.get('senha')
    perfil_id = d.get('perfil_id')
    colaborador_id = d.get('colaborador_id')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if user_id and user_id.strip():
            cur.execute("UPDATE usuarios SET login = %s, senha = %s, perfil_id = %s WHERE id = %s", (login, senha, perfil_id, user_id))
        else:
            cur.execute("INSERT INTO usuarios (login, senha, perfil_id, colaborador_id, nivel_acesso) VALUES (%s, %s, %s, %s, 'USER')", (login, senha, perfil_id, colaborador_id))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('configuracoes'))

# --- CONTRATOS E FINANCEIRO ---
@app.route('/contratos')
def contratos():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, nome_razao FROM clientes ORDER BY nome_razao")
    clientes = cur.fetchall()
    cur.execute("SELECT id, cod_elemento, endereco FROM imoveis ORDER BY cod_elemento")
    imoveis = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('contratos.html', clientes=clientes, imoveis=imoveis)

@app.route('/salvar_contrato', methods=['POST'])
def salvar_contrato():
    if 'usuario_id' not in session: return redirect(url_for('login'))
    d = request.form
    imovel_id = d.get('imovel_id')
    locatario_id = d.get('locatario_id')
    valor_aluguel = float(d.get('valor_aluguel'))
    taxa_adm = float(d.get('taxa_adm', 10.0))
    data_inicio = datetime.strptime(d.get('data_inicio'), '%Y-%m-%d')
    prazo = int(d.get('prazo_meses'))
    dia_venc = int(d.get('dia_vencimento'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO contratos (imovel_id, locatario_id, valor_aluguel, taxa_adm_percentual, data_inicio, prazo_meses, dia_vencimento)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (imovel_id, locatario_id, valor_aluguel, taxa_adm, data_inicio, prazo, dia_venc))
        contrato_id = cur.fetchone()[0]

        for i in range(prazo):
            vencimento = data_inicio + relativedelta(months=i)
            vencimento = vencimento.replace(day=dia_venc)
            comissao = valor_aluguel * (taxa_adm / 100)
            repasse = valor_aluguel - comissao
            cur.execute("""
                INSERT INTO financeiro (contrato_id, numero_parcela, data_vencimento, valor_total, valor_repassar_dono, comissao_imobiliaria)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (contrato_id, i + 1, vencimento, valor_aluguel, repasse, comissao))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return "Contrato e Parcelas Geradas com Sucesso!"

if __name__ == '__main__':
    app.run(debug=True)