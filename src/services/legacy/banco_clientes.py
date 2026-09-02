import sqlite3
import os
import unicodedata

from src.core.database import shared_db_path

DB_PATH = shared_db_path()
DB_DIR = DB_PATH.parent


def conectar_banco():
    os.makedirs(DB_DIR, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def normalizar_texto_busca(valor):
    return str(valor or "").strip()


def normalizar_serial_busca(valor):
    return "".join(ch for ch in normalizar_texto_busca(valor).upper() if ch.isalnum())


def normalizar_matricula_busca(valor):
    texto = normalizar_texto_busca(valor)
    if texto.endswith(".0"):
        texto = texto[:-2]
    return "".join(ch for ch in texto if ch.isdigit())


def normalizar_email_busca(valor):
    texto = normalizar_texto_busca(valor).lower()
    if "@" in texto:
        texto = texto.split("@", 1)[0]
    return texto


def normalizar_telefone_busca(valor):
    return "".join(ch for ch in normalizar_texto_busca(valor) if ch.isdigit())


def normalizar_nome_busca(valor):
    texto = unicodedata.normalize("NFKD", normalizar_texto_busca(valor).lower())
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return " ".join(texto.split())


def classificar_identificador_usuario(identificador):
    texto = normalizar_texto_busca(identificador)
    digitos = "".join(ch for ch in texto if ch.isdigit())

    if len(digitos) == 7 and digitos.startswith("80"):
        return "matricula", digitos
    if len(digitos) == 4 and texto.isdigit():
        return "gsm4", digitos
    if "@" in texto or "." in texto:
        return "email", normalizar_email_busca(texto)
    return "nome", normalizar_nome_busca(texto)


def _garantir_coluna(cursor, tabela, nome_coluna, definicao_sql):
    cursor.execute(f"PRAGMA table_info({tabela})")
    colunas = [row[1] for row in cursor.fetchall()]
    if nome_coluna not in colunas:
        cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome_coluna} {definicao_sql}")


def criar_tabela_clientes():
    with conectar_banco() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                matricula TEXT PRIMARY KEY,
                nome TEXT,
                telefone TEXT,
                email TEXT,
                cargo TEXT
            )
        """)

        _garantir_coluna(cursor, "clientes", "hostname", "TEXT")
        _garantir_coluna(cursor, "clientes", "marca", "TEXT")
        _garantir_coluna(cursor, "clientes", "modelo", "TEXT")
        _garantir_coluna(cursor, "clientes", "serial", "TEXT")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bd_local_ceo_nakrj (
                matricula TEXT PRIMARY KEY,
                nome TEXT,
                email TEXT,
                cargo TEXT,
                categoria TEXT,
                marca TEXT,
                modelo TEXT,
                serial TEXT,
                patrimonio TEXT,
                hostname TEXT,
                mouse TEXT,
                teclado TEXT,
                mochila TEXT,
                suporte TEXT,
                cabo_seguranca TEXT,
                headset TEXT,
                modelo_headset TEXT,
                diretoria TEXT,
                local TEXT,
                data_base TEXT,
                data_criacao TEXT,
                data_atualizacao TEXT,
                data_referencia TEXT,
                source_sheet TEXT,
                source_row INTEGER,
                importado_em TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_bd_local_ceo_nakrj_hostname ON bd_local_ceo_nakrj(hostname)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_bd_local_ceo_nakrj_email ON bd_local_ceo_nakrj(email)"
        )
        for nome_coluna in (
            "mouse",
            "teclado",
            "mochila",
            "suporte",
            "cabo_seguranca",
            "headset",
            "modelo_headset",
            "diretoria",
        ):
            _garantir_coluna(cursor, "bd_local_ceo_nakrj", nome_coluna, "TEXT")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historico_rat (
                source_id TEXT PRIMARY KEY,
                matricula TEXT,
                tecnico TEXT,
                ticket TEXT,
                data_rat TEXT,
                inicio TEXT,
                fim TEXT,
                colaborador TEXT,
                telefone TEXT,
                email TEXT,
                departamento TEXT,
                perfil TEXT,
                outros TEXT,
                substituicao TEXT,
                upgrade TEXT,
                notebook TEXT,
                desktop TEXT,
                impressora TEXT,
                mobile TEXT,
                outro_equipamento TEXT,
                serial_anterior TEXT,
                patrimonio_anterior TEXT,
                hostname_anterior TEXT,
                tag_anterior TEXT,
                imei_anterior TEXT,
                serial TEXT,
                patrimonio TEXT,
                hostname TEXT,
                tag_cliente TEXT,
                imei TEXT,
                backup TEXT,
                perfil_usuario TEXT,
                detalhes_1 TEXT,
                detalhes_2 TEXT,
                aim TEXT,
                kace TEXT,
                outros_softwares TEXT,
                observacoes TEXT,
                descricao_problema TEXT,
                descricao_fechamento TEXT,
                id_docusign TEXT,
                status_docusign TEXT,
                responsavel_arklok TEXT,
                criado_em TEXT,
                importado_em TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_historico_rat_matricula ON historico_rat(matricula)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_historico_rat_ticket ON historico_rat(ticket)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_historico_rat_criado_em ON historico_rat(criado_em)"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notas_fiscais_equipamentos (
                serial TEXT PRIMARY KEY,
                patrimonio TEXT,
                nota_fiscal TEXT,
                source_file TEXT,
                source_row INTEGER,
                importado_em TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_notas_fiscais_patrimonio ON notas_fiscais_equipamentos(patrimonio)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_notas_fiscais_nf ON notas_fiscais_equipamentos(nota_fiscal)"
        )

        conn.commit()


def buscar_cliente_sqlite(matricula):
    with conectar_banco() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                matricula,
                nome,
                telefone,
                email,
                cargo,
                hostname,
                marca,
                modelo,
                serial
            FROM clientes
            WHERE matricula = ?
        """, (matricula,))
        row = cursor.fetchone()

        if not row:
            return None

        return {
            "matricula": row[0] or "",
            "nome": row[1] or "",
            "telefone": row[2] or "",
            "email": row[3] or "",
            "cargo": row[4] or "",
            "hostname": row[5] or "",
            "marca": row[6] or "",
            "modelo": row[7] or "",
            "serial": row[8] or "",
        }


def salvar_ou_atualizar_cliente(dados):
    dados = {
        "matricula": (dados.get("matricula", "") or "").strip(),
        "nome": (dados.get("nome", "") or "").strip(),
        "telefone": (dados.get("telefone", "") or "").strip(),
        "email": (dados.get("email", "") or "").strip(),
        "cargo": (dados.get("cargo", "") or "").strip(),
        "hostname": (dados.get("hostname", "") or "").strip(),
        "marca": (dados.get("marca", "") or "").strip(),
        "modelo": (dados.get("modelo", "") or "").strip(),
        "serial": (dados.get("serial", "") or "").strip(),
    }

    with conectar_banco() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO clientes (
                matricula, nome, telefone, email, cargo,
                hostname, marca, modelo, serial
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(matricula) DO UPDATE SET
                nome = CASE
                    WHEN excluded.nome <> '' THEN excluded.nome
                    ELSE clientes.nome
                END,
                telefone = CASE
                    WHEN excluded.telefone <> '' THEN excluded.telefone
                    ELSE clientes.telefone
                END,
                email = CASE
                    WHEN excluded.email <> '' THEN excluded.email
                    ELSE clientes.email
                END,
                cargo = CASE
                    WHEN excluded.cargo <> '' THEN excluded.cargo
                    ELSE clientes.cargo
                END,
                hostname = CASE
                    WHEN excluded.hostname <> '' THEN excluded.hostname
                    ELSE clientes.hostname
                END,
                marca = CASE
                    WHEN excluded.marca <> '' THEN excluded.marca
                    ELSE clientes.marca
                END,
                modelo = CASE
                    WHEN excluded.modelo <> '' THEN excluded.modelo
                    ELSE clientes.modelo
                END,
                serial = CASE
                    WHEN excluded.serial <> '' THEN excluded.serial
                    ELSE clientes.serial
                END
        """, (
            dados["matricula"],
            dados["nome"],
            dados["telefone"],
            dados["email"],
            dados["cargo"],
            dados["hostname"],
            dados["marca"],
            dados["modelo"],
            dados["serial"],
        ))
        conn.commit()


def salvar_historico_rat(registros):
    if not registros:
        return 0

    criar_tabela_clientes()

    with conectar_banco() as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO historico_rat (
                source_id, matricula, tecnico, ticket, data_rat, inicio, fim,
                colaborador, telefone, email, departamento, perfil, outros,
                substituicao, upgrade, notebook, desktop, impressora, mobile,
                outro_equipamento, serial_anterior, patrimonio_anterior,
                hostname_anterior, tag_anterior, imei_anterior, serial,
                patrimonio, hostname, tag_cliente, imei, backup, perfil_usuario,
                detalhes_1, detalhes_2, aim, kace, outros_softwares,
                observacoes, descricao_problema, descricao_fechamento,
                id_docusign, status_docusign, responsavel_arklok, criado_em
            )
            VALUES (
                :source_id, :matricula, :tecnico, :ticket, :data_rat, :inicio, :fim,
                :colaborador, :telefone, :email, :departamento, :perfil, :outros,
                :substituicao, :upgrade, :notebook, :desktop, :impressora, :mobile,
                :outro_equipamento, :serial_anterior, :patrimonio_anterior,
                :hostname_anterior, :tag_anterior, :imei_anterior, :serial,
                :patrimonio, :hostname, :tag_cliente, :imei, :backup, :perfil_usuario,
                :detalhes_1, :detalhes_2, :aim, :kace, :outros_softwares,
                :observacoes, :descricao_problema, :descricao_fechamento,
                :id_docusign, :status_docusign, :responsavel_arklok, :criado_em
            )
            ON CONFLICT(source_id) DO UPDATE SET
                matricula = excluded.matricula,
                tecnico = excluded.tecnico,
                ticket = excluded.ticket,
                data_rat = excluded.data_rat,
                inicio = excluded.inicio,
                fim = excluded.fim,
                colaborador = excluded.colaborador,
                telefone = excluded.telefone,
                email = excluded.email,
                departamento = excluded.departamento,
                perfil = excluded.perfil,
                outros = excluded.outros,
                substituicao = excluded.substituicao,
                upgrade = excluded.upgrade,
                notebook = excluded.notebook,
                desktop = excluded.desktop,
                impressora = excluded.impressora,
                mobile = excluded.mobile,
                outro_equipamento = excluded.outro_equipamento,
                serial_anterior = excluded.serial_anterior,
                patrimonio_anterior = excluded.patrimonio_anterior,
                hostname_anterior = excluded.hostname_anterior,
                tag_anterior = excluded.tag_anterior,
                imei_anterior = excluded.imei_anterior,
                serial = excluded.serial,
                patrimonio = excluded.patrimonio,
                hostname = excluded.hostname,
                tag_cliente = excluded.tag_cliente,
                imei = excluded.imei,
                backup = excluded.backup,
                perfil_usuario = excluded.perfil_usuario,
                detalhes_1 = excluded.detalhes_1,
                detalhes_2 = excluded.detalhes_2,
                aim = excluded.aim,
                kace = excluded.kace,
                outros_softwares = excluded.outros_softwares,
                observacoes = excluded.observacoes,
                descricao_problema = excluded.descricao_problema,
                descricao_fechamento = excluded.descricao_fechamento,
                id_docusign = excluded.id_docusign,
                status_docusign = excluded.status_docusign,
                responsavel_arklok = excluded.responsavel_arklok,
                criado_em = excluded.criado_em,
                importado_em = CURRENT_TIMESTAMP
        """, registros)
        conn.commit()
        return cursor.rowcount


def salvar_snapshot_bd_local(registros):
    if not registros:
        return 0

    criar_tabela_clientes()

    with conectar_banco() as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO bd_local_ceo_nakrj (
                matricula, nome, email, cargo, categoria, marca, modelo, serial,
                patrimonio, hostname, mouse, teclado, mochila, suporte,
                cabo_seguranca, headset, modelo_headset, diretoria, local,
                data_base, data_criacao, data_atualizacao, data_referencia,
                source_sheet, source_row
            )
            VALUES (
                :matricula, :nome, :email, :cargo, :categoria, :marca, :modelo, :serial,
                :patrimonio, :hostname, :mouse, :teclado, :mochila, :suporte,
                :cabo_seguranca, :headset, :modelo_headset, :diretoria, :local,
                :data_base, :data_criacao, :data_atualizacao, :data_referencia,
                :source_sheet, :source_row
            )
            ON CONFLICT(matricula) DO UPDATE SET
                nome = excluded.nome,
                email = excluded.email,
                cargo = excluded.cargo,
                categoria = excluded.categoria,
                marca = excluded.marca,
                modelo = excluded.modelo,
                serial = excluded.serial,
                patrimonio = excluded.patrimonio,
                hostname = excluded.hostname,
                mouse = excluded.mouse,
                teclado = excluded.teclado,
                mochila = excluded.mochila,
                suporte = excluded.suporte,
                cabo_seguranca = excluded.cabo_seguranca,
                headset = excluded.headset,
                modelo_headset = excluded.modelo_headset,
                diretoria = excluded.diretoria,
                local = excluded.local,
                data_base = excluded.data_base,
                data_criacao = excluded.data_criacao,
                data_atualizacao = excluded.data_atualizacao,
                data_referencia = excluded.data_referencia,
                source_sheet = excluded.source_sheet,
                source_row = excluded.source_row,
                importado_em = CURRENT_TIMESTAMP
        """, registros)
        conn.commit()
        return cursor.rowcount


def salvar_notas_fiscais_equipamentos(registros):
    if not registros:
        return 0

    criar_tabela_clientes()

    with conectar_banco() as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO notas_fiscais_equipamentos (
                serial, patrimonio, nota_fiscal, source_file, source_row
            )
            VALUES (
                :serial, :patrimonio, :nota_fiscal, :source_file, :source_row
            )
            ON CONFLICT(serial) DO UPDATE SET
                patrimonio = excluded.patrimonio,
                nota_fiscal = excluded.nota_fiscal,
                source_file = excluded.source_file,
                source_row = excluded.source_row,
                importado_em = CURRENT_TIMESTAMP
        """, registros)
        conn.commit()
        return cursor.rowcount


def _formatar_registro_bd_local(row):
    return {
        "matricula": row["matricula"] or "",
        "nome": row["nome"] or "",
        "telefone": "",
        "email": row["email"] or "",
        "cargo": row["cargo"] or "",
        "categoria": row["categoria"] or "",
        "marca": row["marca"] or "",
        "modelo": row["modelo"] or "",
        "serial": row["serial"] or "",
        "patrimonio": row["patrimonio"] or "",
        "hostname": row["hostname"] or "",
        "mouse": row["mouse"] or "",
        "teclado": row["teclado"] or "",
        "mochila": row["mochila"] or "",
        "suporte": row["suporte"] or "",
        "cabo_seguranca": row["cabo_seguranca"] or "",
        "headset": row["headset"] or "",
        "modelo_headset": row["modelo_headset"] or "",
        "diretoria": row["diretoria"] or "",
        "local": row["local"] or "",
        "data_base": row["data_base"] or "",
        "data_criacao": row["data_criacao"] or "",
        "data_atualizacao": row["data_atualizacao"] or "",
        "data_referencia": row["data_referencia"] or "",
    }


def buscar_snapshot_bd_local(matricula):
    matricula = normalizar_matricula_busca(matricula)
    if not matricula:
        return None

    with conectar_banco() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                matricula, nome, email, cargo, categoria, marca, modelo, serial,
                patrimonio, hostname, mouse, teclado, mochila, suporte,
                cabo_seguranca, headset, modelo_headset, diretoria, local,
                data_base, data_criacao, data_atualizacao, data_referencia
            FROM bd_local_ceo_nakrj
            WHERE matricula = ?
        """, (matricula,))
        row = cursor.fetchone()
        if not row:
            return None
        return _formatar_registro_bd_local(row)


def buscar_nota_fiscal_por_serial(serial):
    serial_normalizado = normalizar_serial_busca(serial)
    if not serial_normalizado:
        return None

    with conectar_banco() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT serial, patrimonio, nota_fiscal, source_file, source_row, importado_em
                FROM notas_fiscais_equipamentos
                WHERE serial = ?
            """, (serial_normalizado,))
        except sqlite3.OperationalError:
            return None
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "serial": row["serial"] or "",
            "patrimonio": row["patrimonio"] or "",
            "nota_fiscal": row["nota_fiscal"] or "",
            "source_file": row["source_file"] or "",
            "source_row": row["source_row"] or 0,
            "importado_em": row["importado_em"] or "",
        }


def buscar_equipamento_por_serial(serial):
    serial_normalizado = normalizar_serial_busca(serial)
    if not serial_normalizado:
        return None

    resultado = {
        "serial": serial_normalizado,
        "marca": "",
        "modelo": "",
        "patrimonio": "",
        "nota_fiscal": "",
        "hostname": "",
        "categoria": "",
        "matricula": "",
        "nome": "",
        "fonte": "",
    }

    with conectar_banco() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    matricula, nome, categoria, marca, modelo, serial, patrimonio, hostname
                FROM bd_local_ceo_nakrj
                ORDER BY data_referencia DESC, data_atualizacao DESC, data_criacao DESC
            """)
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            serial_row = normalizar_serial_busca(row["serial"] or "")
            if serial_row != serial_normalizado:
                continue
            resultado.update({
                "serial": row["serial"] or serial_normalizado,
                "marca": row["marca"] or "",
                "modelo": row["modelo"] or "",
                "patrimonio": row["patrimonio"] or "",
                "hostname": row["hostname"] or "",
                "categoria": row["categoria"] or "",
                "matricula": row["matricula"] or "",
                "nome": row["nome"] or "",
                "fonte": "bd_local_ceo_nakrj",
            })
            break

        try:
            cursor.execute("""
                SELECT matricula, nome, marca, modelo, serial, hostname
                FROM clientes
            """)
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            serial_row = normalizar_serial_busca(row["serial"] or "")
            if serial_row != serial_normalizado:
                continue
            if not resultado["matricula"]:
                resultado["matricula"] = row["matricula"] or ""
            if not resultado["nome"]:
                resultado["nome"] = row["nome"] or ""
            if not resultado["marca"]:
                resultado["marca"] = row["marca"] or ""
            if not resultado["modelo"]:
                resultado["modelo"] = row["modelo"] or ""
            if not resultado["hostname"]:
                resultado["hostname"] = row["hostname"] or ""
            if not resultado["fonte"]:
                resultado["fonte"] = "clientes"
            if not resultado["serial"]:
                resultado["serial"] = row["serial"] or serial_normalizado
            break

    nota = buscar_nota_fiscal_por_serial(serial_normalizado)
    if nota:
        resultado["patrimonio"] = nota.get("patrimonio", "") or resultado["patrimonio"]
        resultado["nota_fiscal"] = nota.get("nota_fiscal", "") or ""
        if not resultado["fonte"]:
            resultado["fonte"] = "notas_fiscais_equipamentos"

    if resultado["serial"] == serial_normalizado and not any(
        resultado[chave] for chave in ("marca", "modelo", "patrimonio", "nota_fiscal", "hostname", "categoria")
    ):
        return None

    return resultado


def buscar_snapshot_bd_local_por_identificador(identificador, limite=10):
    tipo, valor = classificar_identificador_usuario(identificador)
    if tipo == "gsm4":
        return []

    with conectar_banco() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                matricula, nome, email, cargo, categoria, marca, modelo, serial,
                patrimonio, hostname, mouse, teclado, mochila, suporte,
                cabo_seguranca, headset, modelo_headset, diretoria, local,
                data_base, data_criacao, data_atualizacao, data_referencia
            FROM bd_local_ceo_nakrj
            ORDER BY data_referencia DESC, data_atualizacao DESC, data_criacao DESC, matricula DESC
        """)
        rows = [_formatar_registro_bd_local(row) for row in cursor.fetchall()]

    def corresponde(item):
        if tipo == "matricula":
            return normalizar_matricula_busca(item.get("matricula", "")) == valor
        if tipo == "email":
            return normalizar_email_busca(item.get("email", "")).startswith(valor)
        return valor in normalizar_nome_busca(item.get("nome", ""))

    filtrados = [item for item in rows if corresponde(item)]

    def prioridade(item):
        if tipo == "matricula":
            return 0
        if tipo == "email":
            return 0 if normalizar_email_busca(item.get("email", "")) == valor else 1
        return 0 if normalizar_nome_busca(item.get("nome", "")).startswith(valor) else 1

    filtrados.sort(key=lambda item: item.get("data_referencia", ""), reverse=True)
    filtrados.sort(key=prioridade)
    if limite is not None:
        filtrados = filtrados[:int(limite)]
    return filtrados


def buscar_historico_rat(matricula, limite=None):
    query = """
        SELECT
            source_id,
            matricula,
            tecnico,
            ticket,
            data_rat,
            inicio,
            fim,
            colaborador,
            telefone,
            email,
            departamento,
            perfil,
            outros,
            substituicao,
            upgrade,
            notebook,
            desktop,
            impressora,
            mobile,
            outro_equipamento,
            serial_anterior,
            patrimonio_anterior,
            hostname_anterior,
            tag_anterior,
            imei_anterior,
            serial,
            patrimonio,
            hostname,
            tag_cliente,
            imei,
            backup,
            perfil_usuario,
            detalhes_1,
            detalhes_2,
            aim,
            kace,
            outros_softwares,
            observacoes,
            descricao_problema,
            descricao_fechamento,
            id_docusign,
            status_docusign,
            responsavel_arklok,
            criado_em
        FROM historico_rat
        WHERE matricula = ?
        ORDER BY criado_em DESC, data_rat DESC, source_id DESC
    """

    params = [matricula]
    if limite is not None:
        query += " LIMIT ?"
        params.append(int(limite))

    with conectar_banco() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def buscar_clientes_por_identificador(identificador, limite=10):
    tipo, valor = classificar_identificador_usuario(identificador)
    candidatos_por_matricula = {}

    with conectar_banco() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT matricula, nome, telefone, email, cargo, hostname, marca, modelo, serial
            FROM clientes
        """)
        for row in cursor.fetchall():
            dados = dict(row)
            matricula = normalizar_matricula_busca(dados.get("matricula", ""))
            if not matricula:
                continue
            candidatos_por_matricula[matricula] = {
                "matricula": matricula,
                "nome": dados.get("nome", "") or "",
                "telefone": dados.get("telefone", "") or "",
                "email": dados.get("email", "") or "",
                "cargo": dados.get("cargo", "") or "",
                "hostname": dados.get("hostname", "") or "",
                "marca": dados.get("marca", "") or "",
                "modelo": dados.get("modelo", "") or "",
                "serial": dados.get("serial", "") or "",
                "fonte": "sqlite",
            }

        cursor.execute("""
            SELECT matricula, colaborador, telefone, email, departamento, perfil, hostname, serial
            FROM historico_rat
            ORDER BY rowid DESC
        """)
        for row in cursor.fetchall():
            dados = dict(row)
            matricula = normalizar_matricula_busca(dados.get("matricula", ""))
            if not matricula:
                continue
            base = candidatos_por_matricula.setdefault(matricula, {
                "matricula": matricula,
                "nome": "",
                "telefone": "",
                "email": "",
                "cargo": "",
                "hostname": "",
                "marca": "",
                "modelo": "",
                "serial": "",
                "fonte": "historico_rat",
            })
            if not base["nome"]:
                base["nome"] = dados.get("colaborador", "") or ""
            if not base["telefone"]:
                base["telefone"] = dados.get("telefone", "") or ""
            if not base["email"]:
                base["email"] = dados.get("email", "") or ""
            if not base["cargo"]:
                base["cargo"] = dados.get("perfil", "") or dados.get("departamento", "") or ""
            if not base["hostname"]:
                base["hostname"] = dados.get("hostname", "") or ""
            if not base["serial"]:
                base["serial"] = dados.get("serial", "") or ""

    candidatos = list(candidatos_por_matricula.values())

    def corresponde(item):
        if tipo == "matricula":
            return normalizar_matricula_busca(item.get("matricula", "")) == valor
        if tipo == "gsm4":
            telefone = normalizar_telefone_busca(item.get("telefone", ""))
            return len(telefone) >= 4 and telefone[-4:] == valor
        if tipo == "email":
            email_local = normalizar_email_busca(item.get("email", ""))
            return email_local.startswith(valor)
        nome = normalizar_nome_busca(item.get("nome", ""))
        return valor in nome

    filtrados = [item for item in candidatos if corresponde(item)]

    def chave_ordenacao(item):
        if tipo == "matricula":
            return (0, item.get("nome", ""))
        if tipo == "gsm4":
            telefone = normalizar_telefone_busca(item.get("telefone", ""))
            return (0 if telefone[-4:] == valor else 1, item.get("nome", ""))
        if tipo == "email":
            email_local = normalizar_email_busca(item.get("email", ""))
            prioridade = 0 if email_local == valor else 1
            return (prioridade, item.get("nome", ""))
        nome = normalizar_nome_busca(item.get("nome", ""))
        prioridade = 0 if nome.startswith(valor) else 1
        return (prioridade, item.get("nome", ""))

    filtrados.sort(key=chave_ordenacao)
    if limite is not None:
        filtrados = filtrados[:int(limite)]
    return filtrados
