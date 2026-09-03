"""Seed idempotente das equipes e usuarios da Central NOC.

Logica compartilhada entre o script de linha de comando
(`scripts/seed_noc_teams.py`) e o endpoint `POST /database/seed/noc`, para
que produzam exatamente o mesmo resultado tanto em homologacao (CLI) quanto
em producao (endpoint protegido).

Cria/atualiza `equipes`, `usuarios` e os vinculos `usuario_equipes` a partir
da tabela definida em README_IMPLEMENTACAO_NOC_POR_EQUIPES.md. Nunca apaga
usuarios existentes; roda quantas vezes for preciso sem duplicar nada
(upsert por `codigo` para equipe e por `email` para usuario).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.models.core import Team, User, UserTeam


# (nome, codigo, localizacao)
TEAMS: tuple[tuple[str, str, str], ...] = (
    ("CEO RJ", "CEO_RJ", "Rio de Janeiro/RJ"),
    ("PISA SP", "PISA_SP", "Sao Paulo/SP"),
    ("PERNAMBUCO RE", "PERNAMBUCO_RE", "Recife/PE"),
    ("SIGMA SP", "SIGMA_SP", "Sao Paulo/SP"),
    ("PARANA CO", "PARANA_CO", "Curitiba/PR"),
    ("BRASILIA DF", "BRASILIA_DF", "Brasilia/DF"),
    ("SALVADOR BA", "SALVADOR_BA", "Salvador/BA"),
    ("LUXEMBURGO MG", "LUXEMBURGO_MG", "Belo Horizonte/MG"),
    ("FORTALEZA CE", "FORTALEZA_CE", "Fortaleza/CE"),
    ("SAO CRISTOVAO RJ", "SAO_CRISTOVAO_RJ", "Rio de Janeiro/RJ"),
    ("BELEM PA", "BELEM_PA", "Belem/PA"),
)

# (email, nome, apelido, midiasimples_id, equipe_codigo, perfil_noc)
USERS: tuple[tuple[str, str, str, int | None, str, str], ...] = (
    # Promovidos a `gestor_noc` a pedido explicito do usuario (checkpoint da
    # Etapa 5) - o doc de handoff pede que essa ampliacao de escopo (ver
    # todas as equipes) seja SEMPRE explicita, nunca implicita pro perfil
    # `admin`. Ver README_IMPLEMENTACAO_NOC_POR_EQUIPES.md, secao "Regras de
    # escopo".
    ("marcel.silva@arklok.com.br", "Marcel Diego Silva", "Marcel Silva", 308, "CEO_RJ", "gestor_noc"),
    ("caio.freitas@arklok.com.br", "Caio Vinicius Pereira da Silva Freitas", "Caio Freitas", 332, "CEO_RJ", "gestor_noc"),
    ("michel.delocco@arklok.com.br", "Michel Purcina Delocco", "Michel Delocco", 148, "CEO_RJ", "gestor_noc"),
    ("marcos.reis@arklok.com.br", "Marcos Paulo da Silva Reis", "Marcos Reis", 227, "CEO_RJ", "gestor_noc"),
    ("brunosilva3@unigranrio.br", "Bruno Rodrigues da Silva", "Bruno Rodrigues", None, "CEO_RJ", "tecnico"),
    ("thiago.brandelik@arklok.com.br", "Thiago Brandelik", "Thiago Brandelik", None, "PISA_SP", "tecnico"),
    ("mateus.bispo@arklok.com.br", "Mateus Santos Bispo", "Mateus Bispo", None, "PERNAMBUCO_RE", "tecnico"),
    ("ranieri.linhares@arklok.com.br", "Ranieri Silva Linhares", "Ranieri Linhares", None, "SIGMA_SP", "tecnico"),
    ("fernando.rodrigues@arktecnico.com.br", "Fernando Rodrigues", "Fernando Rodrigues", None, "PISA_SP", "tecnico"),
    ("guilherme.oliveira@arklok.com.br", "Guilherme Carvalho de Oliveira", "Guilherme Oliveira", None, "PARANA_CO", "tecnico"),
    ("leonardo.cardoso@arklok.com.br", "Leonardo Martins Cardoso", "Leonardo Cardoso", None, "BRASILIA_DF", "tecnico"),
    ("danilo.carvalho@arklok.com.br", "Danilo Oliveira Carvalho", "Danilo Carvalho", None, "SALVADOR_BA", "tecnico"),
    ("jhonatas.santos@arklok.com.br", "Jhonatas da Silva Santos", "Jhonatas Santos", None, "PISA_SP", "tecnico"),
    ("patrick.costa@arklok.com.br", "Patrick da Costa Cruz", "Patrick Costa", None, "LUXEMBURGO_MG", "tecnico"),
    ("thiago.vidal@arklok.com.br", "Thiago Monteiro Vidal", "Thiago Vidal", None, "FORTALEZA_CE", "tecnico"),
    ("kauan.oliveira@arklok.com.br", "Kauan Mendes de Oliveira", "Kauan Oliveira", None, "PISA_SP", "tecnico"),
    ("rafael.pereira@arklok.com.br", "Rafael de Souza Pereira", "Rafael Pereira", None, "SAO_CRISTOVAO_RJ", "tecnico"),
    ("kauam.ferreira@arklok.com.br", "Kauam Ferreira do Carmo", "Kauam Ferreira", None, "PISA_SP", "tecnico"),
    ("luis.gomes@arklok.com.br", "Luis Carlos de Lima Gomes", "Luis Gomes", None, "SAO_CRISTOVAO_RJ", "tecnico"),
    ("pablo.souza@arklok.com.br", "Pablo Jose Reis Souza", "Pablo Souza", None, "BELEM_PA", "tecnico"),
    ("hudson.pereira@arklok.com.br", "Hudson Vitor dos Santos Pereira", "Hudson Pereira", None, "PISA_SP", "tecnico"),
)


def seed_noc_teams(db: Session) -> dict[str, int]:
    """Aplica o seed idempotente usando a sessao SQLAlchemy informada.

    Nao faz commit/close: quem chama controla a transacao (permite reuso
    tanto pelo script CLI, que abre sua propria sessao, quanto pelo
    endpoint da API, que recebe a sessao via dependency injection).
    """
    stats = {
        "teams_created": 0,
        "teams_updated": 0,
        "users_created": 0,
        "users_updated": 0,
        "links_created": 0,
        "links_updated": 0,
    }

    teams_by_code: dict[str, Team] = {}
    for nome, codigo, localizacao in TEAMS:
        team = db.query(Team).filter(Team.codigo == codigo).first()
        if team:
            changed = False
            if team.nome != nome:
                team.nome = nome
                changed = True
            if team.localizacao != localizacao:
                team.localizacao = localizacao
                changed = True
            if not team.ativa:
                team.ativa = True
                changed = True
            if changed:
                stats["teams_updated"] += 1
        else:
            team = Team(nome=nome, codigo=codigo, localizacao=localizacao, ativa=True)
            db.add(team)
            db.flush()
            stats["teams_created"] += 1
        teams_by_code[codigo] = team

    for email, nome, apelido, midiasimples_id, equipe_codigo, perfil_noc in USERS:
        user = db.query(User).filter(User.email == email).first()
        if user:
            changed = False
            if user.nome != nome:
                user.nome = nome
                changed = True
            if user.apelido != apelido:
                user.apelido = apelido
                changed = True
            if midiasimples_id is not None and user.midiasimples_id != midiasimples_id:
                user.midiasimples_id = midiasimples_id
                changed = True
            if user.perfil_noc != perfil_noc:
                user.perfil_noc = perfil_noc
                changed = True
            if not user.ativo:
                user.ativo = True
                changed = True
            if changed:
                stats["users_updated"] += 1
        else:
            user = User(
                nome=nome,
                apelido=apelido,
                email=email,
                midiasimples_id=midiasimples_id,
                perfil="tecnico",
                perfil_noc=perfil_noc,
                ativo=True,
            )
            db.add(user)
            db.flush()
            stats["users_created"] += 1

        team = teams_by_code[equipe_codigo]
        link = (
            db.query(UserTeam)
            .filter(UserTeam.usuario_id == user.id, UserTeam.equipe_id == team.id)
            .first()
        )
        if link:
            changed = False
            if not link.ativa:
                link.ativa = True
                changed = True
            if not link.principal:
                link.principal = True
                changed = True
            if changed:
                stats["links_updated"] += 1
        else:
            db.add(UserTeam(usuario_id=user.id, equipe_id=team.id, ativa=True, principal=True))
            stats["links_created"] += 1

    return stats
