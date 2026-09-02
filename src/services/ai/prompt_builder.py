SYSTEM_PROMPT = (
    "Voce auxilia tecnicos do MD HUB FINAL. Responda somente em portugues do Brasil. "
    "Use exclusivamente os fatos fornecidos. Nao invente IDs, defeitos, datas, diagnosticos "
    "ou conclusoes. Nunca decida uso inadequado. Nao inclua raciocinio interno."
)


def _messages(user_content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def review_report(text: str, document_type: str, usage_inappropriate_selected: bool):
    usage_rule = (
        "O campo separado de uso inadequado foi marcado pelo tecnico. Isso e apenas contexto: "
        "nao acrescente essa informacao ao texto se ela nao estiver no original."
        if usage_inappropriate_selected
        else
        "O campo separado de uso inadequado nao foi marcado. Nao mencione esse campo na resposta."
    )
    prompt = (
        f"Tarefa: revisar a redacao de um {document_type}. Corrija somente ortografia, concordancia, "
        "pontuacao, clareza e padronizacao. Preserve todos os fatos e o sentido do texto original. "
        f"{usage_rule} Nao comente as instrucoes, os campos do formulario ou a qualidade do texto. "
        "Se o original for curto, devolva-o curto. Entregue exclusivamente o texto revisado entre "
        f"as marcas abaixo, sem titulo nem explicacao.\n<texto_original>\n{text}\n</texto_original>"
    )
    return _messages(prompt), "review-report-v2"


def summarize(text: str):
    prompt = (
        "Tarefa: produzir um resumo operacional fiel. Identifique somente os fatos presentes no texto, "
        "preserve resultados ausentes ou inconclusivos e nao acrescente causa, diagnostico ou conclusao. "
        "Nao comente as instrucoes. Entregue exclusivamente o resumo, sem titulo nem explicacao.\n"
        f"<ocorrencia_original>\n{text}\n</ocorrencia_original>"
    )
    return _messages(prompt), "operational-summary-v2"


def replacement_script(replacement_type: str, known_facts: list[str]):
    facts = "\n".join(f"- {fact}" for fact in known_facts)
    prompt = (
        f"Tarefa: redigir um roteiro operacional curto para {replacement_type}. Use todos e somente os "
        "fatos fornecidos abaixo. Organize-os em uma sequencia clara de acoes, sem acrescentar "
        "identificadores, datas, causas, diagnosticos ou etapas nao informadas. Nao comente as instrucoes. "
        f"Entregue exclusivamente o roteiro, sem titulo nem explicacao.\n<fatos_conhecidos>\n{facts}\n</fatos_conhecidos>"
    )
    return _messages(prompt), "replacement-script-v2"
