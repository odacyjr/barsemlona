from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from http import cookies
from pathlib import Path
import hashlib
import json
import os
import secrets
import sqlite3

ROOT = Path(__file__).parent
DB_PATH = Path(os.environ.get("DATABASE_PATH", ROOT / "data" / "fut-dos-cria.db"))
# Configure this secret in Render (or locally) before enabling administrator login.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
SESSIONS = {}
DEFAULT_RULES = [
    ("Objetivo", "A pelada promove diversão, amizade, respeito e competitividade saudável. Todos devem respeitar este regulamento."),
    ("Horário", "O horário informado é para todos estarem prontos para jogar. O sorteio dos times será realizado no horário marcado."),
    ("Confirmação de presença", "Quem colocar o nome na lista assume o compromisso de comparecer. Faltas sem aviso geram advertência, suspensão de uma pelada na segunda ocorrência e duas peladas na terceira."),
    ("Tempo das partidas", "Cada jogo termina aos 8 minutos ou quando o primeiro time marcar 2 gols. Em empate, permanece a equipe que entrou por último; na primeira rodada, permanece a equipe de menor número."),
    ("Organização da fila", "O time vencedor permanece em campo, o derrotado sai e entra o próximo time da fila. Em empate, vale a regra definida pela organização."),
    ("Conduta e respeito", "Respeito com companheiros, adversários, organizadores, árbitro e funcionários da arena é obrigatório. Discussões são normais; desrespeito não."),
    ("Brigas", "São proibidas agressões, ameaças, empurrões, socos, chutes ou tentativas de confusão. A organização pode expulsar, suspender ou banir envolvidos."),
    ("Faltas e cartões", "Cartão azul: 2 minutos fora por entradas fortes, carrinhos perigosos ou reclamações. Vermelho: expulsão do dia por agressão, violência ou ofensas graves."),
    ("Fair Play", "Ajude quando a bola sair, evite discussões, reconheça faltas e respeite decisões. Sem goleiro, um jogador do time de fora deve ajudar no gol."),
    ("Alteração da lista", "O confirmado pode retirar o nome até 17h do dia da pelada. Após isso, a ausência é falta sem aviso, salvo força maior analisada pela organização."),
    ("Vagas para diaristas", "Na ausência de mensalista, a vaga pode ser destinada a diarista, por ordem de pagamento da diária de R$ 20,00."),
    ("Destinação das diárias", "Valores de diárias beneficiam a pelada: bolas, coletes, premiações, confraternizações e outros materiais necessários."),
    ("Transparência financeira", "Receitas e despesas devem ser registradas com descrição e valor. Mensalistas podem solicitar a prestação de contas a qualquer momento."),
    ("Mensalistas e renovação", "Mensalistas atuais têm prioridade na renovação se pagarem no prazo. Vagas não renovadas ficam disponíveis a novos integrantes."),
    ("Nova vaga mensalista", "A prioridade é de diaristas mais frequentes, comprometidos, pontuais e respeitosos. A administração define prazo para pagamento."),
    ("Sorteio das equipes", "O sorteio das equipes e de quem inicia é transparente, aleatório e soberano."),
    ("Formação das equipes", "Times são montados de 1 a 5 estrelas para equilibrar os níveis. Pequenas diferenças podem ocorrer."),
    ("Permanência das equipes", "Após o sorteio, os times permanecem os mesmos durante a pelada, salvo desequilíbrio excepcional aprovado por todos e pela organização."),
]


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    if "role" not in {row[1] for row in conn.execute("PRAGMA table_info(users)")}:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    conn.execute("""CREATE TABLE IF NOT EXISTS finance_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        description TEXT NOT NULL,
        amount REAL NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('income', 'expense')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, position TEXT NOT NULL,
        foot TEXT NOT NULL, stars INTEGER NOT NULL, phrase TEXT NOT NULL DEFAULT '', goals INTEGER NOT NULL DEFAULT 0,
        assists INTEGER NOT NULL DEFAULT 0, games INTEGER NOT NULL DEFAULT 0
    )""")
    player_columns = {row[1] for row in conn.execute("PRAGMA table_info(players)")}
    if "photo_url" not in player_columns:
        conn.execute("ALTER TABLE players ADD COLUMN photo_url TEXT NOT NULL DEFAULT ''")
    if "jersey_number" not in player_columns:
        conn.execute("ALTER TABLE players ADD COLUMN jersey_number INTEGER")
    conn.execute("""CREATE TABLE IF NOT EXISTS rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, content TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS waitlist (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS attendance (
        player_name TEXT PRIMARY KEY,
        confirmed INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pendente' CHECK(status IN ('confirmado', 'ausente', 'pendente')),
        justificativa TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        data_atualizacao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    attendance_columns = {row[1] for row in conn.execute("PRAGMA table_info(attendance)")}
    if "status" not in attendance_columns:
        conn.execute("ALTER TABLE attendance ADD COLUMN status TEXT NOT NULL DEFAULT 'pendente'")
    if "justificativa" not in attendance_columns:
        conn.execute("ALTER TABLE attendance ADD COLUMN justificativa TEXT NOT NULL DEFAULT ''")
    if "data_atualizacao" not in attendance_columns:
        conn.execute("ALTER TABLE attendance ADD COLUMN data_atualizacao TEXT")
    if "admin_confirmed" not in attendance_columns:
        conn.execute("ALTER TABLE attendance ADD COLUMN admin_confirmed INTEGER NOT NULL DEFAULT 0")
    conn.execute("UPDATE attendance SET status=CASE WHEN confirmed=1 THEN 'confirmado' ELSE 'pendente' END WHERE status IS NULL OR status='pendente'")
    conn.execute("UPDATE attendance SET data_atualizacao=COALESCE(data_atualizacao, updated_at, CURRENT_TIMESTAMP)")
    conn.commit()
    if conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0:
        conn.executemany("INSERT INTO rules(title,content) VALUES(?,?)", DEFAULT_RULES)
        conn.commit()
    return conn


def password_hash(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, status, payload, session_token=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if session_token:
            cookie = cookies.SimpleCookie()
            cookie["fut_session"] = session_token
            cookie["fut_session"]["path"] = "/"
            cookie["fut_session"]["httponly"] = True
            cookie["fut_session"]["samesite"] = "Lax"
            self.send_header("Set-Cookie", cookie.output(header="").strip())
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        size = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(size) or b"{}")
        except json.JSONDecodeError:
            return {}

    def session(self):
        cookie = cookies.SimpleCookie(self.headers.get("Cookie"))
        token = cookie.get("fut_session")
        return SESSIONS.get(token.value) if token else None

    def do_GET(self):
        if self.path == "/api/session":
            return self.send_json(200, {"user": self.session()})
        if self.path == "/api/finance":
            if self.session() and self.session().get("role") == "diarist":
                return self.send_json(403, {"error": "Diaristas não têm acesso ao financeiro."})
            conn = db()
            rows = [dict(row) for row in conn.execute("SELECT * FROM finance_transactions ORDER BY id DESC").fetchall()]
            conn.close()
            income = sum(row["amount"] for row in rows if row["kind"] == "income")
            expense = sum(row["amount"] for row in rows if row["kind"] == "expense")
            return self.send_json(200, {"transactions": rows, "income": income, "expense": expense, "balance": income - expense})
        if self.path == "/api/players":
            conn = db()
            rows = [dict(row) for row in conn.execute("SELECT * FROM players ORDER BY id DESC").fetchall()]
            conn.close()
            return self.send_json(200, {"players": rows})
        if self.path == "/api/profile":
            user = self.session()
            if not user or user.get("role") != "user":
                return self.send_json(403, {"error": "Apenas mensalistas possuem perfil de jogador."})
            conn = db()
            player = conn.execute("SELECT * FROM players WHERE name=? COLLATE NOCASE", (user["name"],)).fetchone()
            conn.close()
            if not player:
                return self.send_json(404, {"error": "Perfil de jogador não encontrado."})
            return self.send_json(200, {"player": dict(player)})
        if self.path == "/api/rules":
            if self.session() and self.session().get("role") == "diarist":
                return self.send_json(403, {"error": "Diaristas não têm acesso às regras."})
            conn = db()
            rows = [dict(row) for row in conn.execute("SELECT * FROM rules ORDER BY id").fetchall()]
            conn.close()
            return self.send_json(200, {"rules": rows})
        if self.path == "/api/waitlist":
            if not self.session() or self.session().get("role") != "user": return self.send_json(403, {"error":"Apenas mensalistas acessam a lista de espera."})
            conn=db(); rows=[dict(row) for row in conn.execute("SELECT * FROM waitlist ORDER BY id").fetchall()]; conn.close(); return self.send_json(200, {"waitlist":rows})
        if self.path == "/api/diarists":
            user = self.session()
            is_admin = bool(user and user.get("role") == "admin")
            conn = db()
            rows = [dict(row) for row in conn.execute("""SELECT users.name, COALESCE(attendance.status, 'pendente') AS status,
                COALESCE(attendance.justificativa, '') AS justificativa, attendance.data_atualizacao,
                COALESCE(attendance.admin_confirmed, 0) AS admin_confirmed
                FROM users LEFT JOIN attendance ON attendance.player_name = users.name COLLATE NOCASE
                WHERE users.role='diarist'
                ORDER BY CASE WHEN attendance.status='confirmado' THEN 0 ELSE 1 END,
                attendance.data_atualizacao ASC, users.name COLLATE NOCASE""").fetchall()]
            conn.close()
            if not is_admin:
                for row in rows:
                    row["justificativa"] = ""
            return self.send_json(200, {"diarists": rows})
        if self.path == "/api/attendance":
            conn = db()
            rows = [dict(row) for row in conn.execute("SELECT player_name,status,justificativa,data_atualizacao,admin_confirmed FROM attendance").fetchall()]
            confirmed_names = [row["player_name"] for row in rows if row["status"] == "confirmado"]
            user = self.session()
            mine = {"status": "pendente", "justificativa": ""}
            if user and user.get("role") in ("user", "diarist"):
                mine = next(({"status": row["status"], "justificativa": row["justificativa"]} for row in rows if row["player_name"].lower() == user["name"].lower()), mine)
            conn.close()
            return self.send_json(200, {"confirmed": len(confirmed_names), "mine": mine["status"] == "confirmado", "my_attendance": mine, "confirmed_names": confirmed_names, "records": rows})
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/signup":
            data = self.body()
            name, password = data.get("name", "").strip(), data.get("password", "")
            account_type = "diarist" if data.get("account_type") == "diarist" else "user"
            position, foot, phrase = data.get("position", "").strip(), data.get("foot", "").strip(), data.get("phrase", "").strip()
            stars = int(data.get("stars", 3) or 3)
            valid_positions = {"Goleiro", "Fixo", "Ala direito", "Ala esquerdo", "Meia", "Pivô", "Atacante"}
            if not name or len(password) < 6:
                return self.send_json(400, {"error": "Preencha nome e uma senha de ao menos 6 caracteres."})
            if account_type == "user" and (position not in valid_positions or foot not in {"Canhoto", "Destro", "Ambidestro"} or not phrase):
                return self.send_json(400, {"error": "Preencha posição, pé dominante e frase motivacional."})
            try:
                conn = db()
                if conn.execute("SELECT 1 FROM users WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
                    conn.close()
                    return self.send_json(409, {"error": "Este nome já está cadastrado."})
                internal_email = f"{secrets.token_urlsafe(12)}@futdoscria.local"
                conn.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)", (name, internal_email, password_hash(password), account_type))
                if account_type == "user":
                    conn.execute("INSERT INTO players(name,position,foot,stars,phrase) VALUES(?,?,?,?,?)", (name, position, foot, stars, phrase))
                conn.commit(); conn.close()
            except sqlite3.IntegrityError:
                return self.send_json(409, {"error": "Não foi possível criar este cadastro."})
            return self.send_json(201, {"message": "Cadastro realizado. Agora entre com sua conta."})

        if self.path == "/api/login":
            data = self.body()
            if data.get("admin"):
                if not ADMIN_PASSWORD or data.get("password") != ADMIN_PASSWORD:
                    return self.send_json(401, {"error": "Senha de administrador incorreta."})
                user = {"name": "Administrador", "role": "admin"}
            else:
                name, password = data.get("name", "").strip(), data.get("password", "")
                conn = db()
                row = conn.execute("SELECT name, password_hash, role FROM users WHERE name=? COLLATE NOCASE", (name,)).fetchone()
                conn.close()
                if not row or row["password_hash"] != password_hash(password):
                    return self.send_json(401, {"error": "Nome ou senha incorretos."})
                requested_role = data.get("account_type")
                if requested_role and requested_role != row["role"]:
                    return self.send_json(403, {"error": "O tipo de conta selecionado não corresponde a este cadastro."})
                user = {"name": row["name"], "role": row["role"]}
            token = secrets.token_urlsafe(32)
            SESSIONS[token] = user
            return self.send_json(200, {"user": user}, token)

        if self.path == "/api/finance":
            if not self.session() or self.session().get("role") != "admin":
                return self.send_json(403, {"error": "Apenas o administrador pode lançar movimentações."})
            data = self.body()
            description = data.get("description", "").strip()
            kind = data.get("kind")
            try:
                amount = float(data.get("amount", 0))
            except (TypeError, ValueError):
                amount = 0
            if not description or kind not in ("income", "expense") or amount <= 0:
                return self.send_json(400, {"error": "Informe descrição, tipo e um valor maior que zero."})
            conn = db()
            conn.execute("INSERT INTO finance_transactions(description, amount, kind) VALUES(?,?,?)", (description, amount, kind))
            conn.commit()
            conn.close()
            return self.send_json(201, {"message": "Movimentação registrada."})

        if self.path == "/api/players":
            if not self.session() or self.session().get("role") != "admin":
                return self.send_json(403, {"error": "Apenas o administrador pode cadastrar jogadores."})
            data = self.body()
            name, position, foot = data.get("name", "").strip(), data.get("position", "").strip(), data.get("foot", "").strip()
            try: stars = int(data.get("stars", 0))
            except (TypeError, ValueError): stars = 0
            try: jersey_number = int(data.get("jersey_number", 0))
            except (TypeError, ValueError): jersey_number = 0
            if not name or not position or not foot or not 1 <= stars <= 5 or not 1 <= jersey_number <= 99:
                return self.send_json(400, {"error": "Preencha nome, número da camisa, posição, perna boa e estrelas."})
            conn = db()
            password = data.get("password", "")
            if password:
                if len(password) < 6: conn.close(); return self.send_json(400, {"error": "A senha precisa ter ao menos 6 caracteres."})
                if conn.execute("SELECT 1 FROM users WHERE name=? COLLATE NOCASE", (name,)).fetchone(): conn.close(); return self.send_json(409, {"error": "Já existe uma conta com este nome."})
                conn.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)", (name, f"{secrets.token_urlsafe(12)}@futdoscria.local", password_hash(password), "user"))
            conn.execute("INSERT INTO players(name,position,foot,stars,phrase,photo_url,jersey_number) VALUES(?,?,?,?,?,?,?)", (name, position, foot, stars, data.get("phrase", "").strip(), data.get("photo_url", "").strip(), jersey_number))
            conn.commit(); conn.close()
            return self.send_json(201, {"message": "Jogador e conta cadastrados." if password else "Jogador cadastrado."})

        parts = self.path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "players"] and parts[3] == "stats":
            if not self.session() or self.session().get("role") != "admin":
                return self.send_json(403, {"error": "Apenas o administrador pode alterar as estatísticas."})
            try:
                player_id = int(parts[2])
                data = self.body()
                goals = int(data.get("goals", -1))
                assists = int(data.get("assists", -1))
                games = int(data.get("games", -1))
                stars = int(data.get("stars", 0))
                jersey_number = int(data.get("jersey_number", 0))
            except (TypeError, ValueError):
                return self.send_json(400, {"error": "Informe estrelas, gols e assistências válidos."})
            if not 1 <= stars <= 5 or goals < 0 or assists < 0 or games < 0 or not 1 <= jersey_number <= 99:
                return self.send_json(400, {"error": "Use número de camisa entre 1 e 99, estrelas de 1 a 5 e estatísticas não negativas."})
            conn = db()
            cursor = conn.execute("UPDATE players SET stars=?, goals=?, assists=?, games=?, jersey_number=? WHERE id=?", (stars, goals, assists, games, jersey_number, player_id))
            conn.commit(); conn.close()
            if not cursor.rowcount:
                return self.send_json(404, {"error": "Jogador não encontrado."})
            return self.send_json(200, {"message": "Estrelas e estatísticas atualizadas."})

        if self.path == "/api/profile":
            user = self.session()
            if not user or user.get("role") != "user":
                return self.send_json(403, {"error": "Apenas mensalistas podem alterar o perfil."})
            data = self.body()
            name = data.get("name", "").strip()
            position = data.get("position", "").strip()
            foot = data.get("foot", "").strip()
            phrase = data.get("phrase", "").strip()
            new_password = data.get("password", "")
            photo_url = data.get("photo_url", "").strip()
            valid_positions = {"Goleiro", "Fixo", "Ala direito", "Ala esquerdo", "Meia", "Pivô", "Atacante"}
            if len(name) < 2 or len(name) > 80 or position not in valid_positions or foot not in {"Canhoto", "Destro", "Ambidestro"} or not phrase:
                return self.send_json(400, {"error": "Preencha nome, posição, pé dominante e frase motivacional."})
            if new_password and len(new_password) < 6:
                return self.send_json(400, {"error": "A nova senha precisa ter ao menos 6 caracteres."})
            if photo_url and (not photo_url.startswith("data:image/") or len(photo_url) > 3_000_000):
                return self.send_json(400, {"error": "Envie uma imagem válida de até 2 MB."})
            old_name = user["name"]
            conn = db()
            player = conn.execute("SELECT id FROM players WHERE name=? COLLATE NOCASE", (old_name,)).fetchone()
            if not player:
                conn.close()
                return self.send_json(404, {"error": "Perfil de jogador não encontrado."})
            if name.lower() != old_name.lower() and conn.execute("SELECT 1 FROM users WHERE name=? COLLATE NOCASE", (name,)).fetchone():
                conn.close()
                return self.send_json(409, {"error": "Esse nome já está em uso."})
            if name != old_name:
                conn.execute("UPDATE users SET name=? WHERE name=? COLLATE NOCASE", (name, old_name))
                conn.execute("UPDATE attendance SET player_name=? WHERE player_name=? COLLATE NOCASE", (name, old_name))
            if new_password:
                conn.execute("UPDATE users SET password_hash=? WHERE name=? COLLATE NOCASE", (password_hash(new_password), name))
            conn.execute("UPDATE players SET name=?, position=?, foot=?, phrase=?, photo_url=? WHERE id=?", (name, position, foot, phrase, photo_url, player["id"]))
            conn.commit(); conn.close()
            for session in SESSIONS.values():
                if session.get("role") == "user" and session.get("name", "").lower() == old_name.lower():
                    session["name"] = name
            user["name"] = name
            return self.send_json(200, {"message": "Perfil atualizado.", "user": user})

        if self.path == "/api/rules":
            if not self.session() or self.session().get("role") != "admin":
                return self.send_json(403, {"error": "Apenas o administrador pode criar regras."})
            data = self.body(); title, content = data.get("title", "").strip(), data.get("content", "").strip()
            if not title or not content:
                return self.send_json(400, {"error": "Informe o título e o texto da regra."})
            conn = db(); conn.execute("INSERT INTO rules(title,content) VALUES(?,?)", (title, content)); conn.commit(); conn.close()
            return self.send_json(201, {"message": "Regra criada."})

        if self.path == "/api/waitlist":
            if not self.session() or self.session().get("role") != "user": return self.send_json(403, {"error":"Apenas mensalistas podem adicionar diaristas."})
            name=self.body().get("name","").strip()
            if not name: return self.send_json(400, {"error":"Informe o nome."})
            conn=db(); conn.execute("INSERT INTO waitlist(name) VALUES(?)",(name,)); conn.commit(); conn.close(); return self.send_json(201, {"message":"Diarista adicionado à lista de espera."})
        if self.path in ("/api/attendance", "/api/attendance/admin"):
            user = self.session()
            is_admin = self.path == "/api/attendance/admin"
            if not user or (is_admin and user.get("role") != "admin") or (not is_admin and user.get("role") not in ("user", "diarist")):
                return self.send_json(403, {"error": "Você não tem permissão para atualizar esta presença."})
            data = self.body()
            status = data.get("status")
            if status is None:  # compatibilidade com registros enviados pela versão anterior
                status = "confirmado" if data.get("confirmed") else "ausente"
            if status not in ("confirmado", "ausente", "pendente"):
                return self.send_json(400, {"error": "Status de presença inválido."})
            justification = data.get("justificativa", "").strip()
            if len(justification) > 500:
                return self.send_json(400, {"error": "A justificativa pode ter no máximo 500 caracteres."})
            if status == "confirmado":
                justification = ""
            player_name = data.get("player_name", "").strip() if is_admin else user["name"]
            conn = db()
            if is_admin:
                player = conn.execute("SELECT name FROM players WHERE name=? COLLATE NOCASE", (player_name,)).fetchone()
                if not player:
                    conn.close()
                    return self.send_json(404, {"error": "Jogador não encontrado."})
                player_name = player["name"]
            supervision = 1 if is_admin or user.get("role") != "diarist" else 0
            conn.execute("""INSERT INTO attendance(player_name,confirmed,status,justificativa,updated_at,data_atualizacao,admin_confirmed)
                VALUES(?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?)
                ON CONFLICT(player_name) DO UPDATE SET confirmed=excluded.confirmed,status=excluded.status,
                justificativa=excluded.justificativa,updated_at=CURRENT_TIMESTAMP,data_atualizacao=CURRENT_TIMESTAMP,
                admin_confirmed=excluded.admin_confirmed""",
                (player_name, int(status == "confirmado"), status, justification, supervision))
            conn.commit(); conn.close()
            return self.send_json(200, {"message": "Presença atualizada.", "status": status, "justificativa": justification})

        if self.path == "/api/attendance/admin/approve":
            user = self.session()
            if not user or user.get("role") != "admin":
                return self.send_json(403, {"error": "Apenas o administrador pode aprovar confirmações."})
            player_name = self.body().get("player_name", "").strip()
            conn = db()
            diarist = conn.execute("SELECT name FROM users WHERE name=? COLLATE NOCASE AND role='diarist'", (player_name,)).fetchone()
            if not diarist:
                conn.close()
                return self.send_json(404, {"error": "Diarista não encontrado."})
            row = conn.execute("SELECT status FROM attendance WHERE player_name=? COLLATE NOCASE", (diarist["name"],)).fetchone()
            if not row or row["status"] != "confirmado":
                conn.close()
                return self.send_json(400, {"error": "O diarista precisa estar confirmado antes da aprovação."})
            conn.execute("UPDATE attendance SET admin_confirmed=1, data_atualizacao=CURRENT_TIMESTAMP WHERE player_name=? COLLATE NOCASE", (diarist["name"],))
            conn.commit(); conn.close()
            return self.send_json(200, {"message": "Confirmação do diarista aprovada."})

        if self.path == "/api/logout":
            cookie = cookies.SimpleCookie(self.headers.get("Cookie"))
            token = cookie.get("fut_session")
            if token:
                SESSIONS.pop(token.value, None)
            return self.send_json(200, {"message": "Sessão encerrada."}, "expired")

        return self.send_json(404, {"error": "Rota não encontrada."})

    def do_DELETE(self):
        user = self.session()
        if not user or user.get("role") != "admin":
            return self.send_json(403, {"error": "Apenas o administrador pode excluir itens."})
        parts = self.path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "api" or parts[1] not in ("players", "rules", "finance"):
            return self.send_json(404, {"error": "Rota não encontrada."})
        try: item_id = int(parts[2])
        except ValueError: return self.send_json(400, {"error": "Identificador inválido."})
        table = {"players": "players", "rules": "rules", "finance": "finance_transactions"}[parts[1]]
        conn = db(); conn.execute(f"DELETE FROM {table} WHERE id=?", (item_id,)); conn.commit(); conn.close()
        return self.send_json(200, {"message": "Item excluído."})


if __name__ == "__main__":
    db().close()
    print("Barsemlona em http://localhost:8000")

PORT = int(os.environ.get("PORT", 8000))
ThreadingHTTPServer(("0.0.0.0", PORT), AppHandler).serve_forever()
