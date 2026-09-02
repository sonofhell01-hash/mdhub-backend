from __future__ import annotations


DOCUMENT_LABELS = {
    "rat": "Relatório de Atendimento Técnico (RAT)",
    "laudo": "Laudo Técnico",
    "devolucao": "Termo de Devolução",
    "emprestimo": "Termo de Empréstimo",
    "substituicao": "Contratos do Colaborador Tim",
    "substituicao_headset": "Termo de Substituição de Headset",
    "concessao": "Termo de Concessão",
    "rollout": "Termo de Rollout",
    "fechamento": "Fechamento Operacional",
}


class MessageBuilder:
    def signature_reminder(self, *, nome: str, tipo_documento: str, numero_documento: str) -> str:
        label = DOCUMENT_LABELS.get(tipo_documento, tipo_documento.upper())
        return (
            f"Olá *{nome}*, tudo bem?\n\n"
            f"Seu {label} *{numero_documento}* está aguardando sua assinatura eletrônica.\n\n"
            "Por favor, verifique seu e-mail (remetente: *Arkia Arklok*) e assine o documento "
            "para finalizarmos seu atendimento.\n\n"
            "A assinatura é importante para o fechamento do chamado e para não comprometer nosso SLA.\n\n"
            "Se já assinou, desconsidere esta mensagem.\n\n"
            "Atenciosamente,\n"
            "*Equipe Arklok*\n\n"
            "---\n"
            "Esta é uma mensagem automática. Não responda este número."
        )

    def signature_reminder_many(self, *, nome: str, documentos: list[dict[str, str]]) -> str:
        valid_documents = [
            {
                "tipo_documento": str(item.get("tipo_documento") or "").strip(),
                "numero_documento": str(item.get("numero_documento") or "").strip(),
            }
            for item in documentos
            if str(item.get("tipo_documento") or "").strip() and str(item.get("numero_documento") or "").strip()
        ]
        if len(valid_documents) <= 1:
            item = valid_documents[0] if valid_documents else {"tipo_documento": "documento", "numero_documento": "manual"}
            return self.signature_reminder(
                nome=nome,
                tipo_documento=item["tipo_documento"],
                numero_documento=item["numero_documento"],
            )

        lines = []
        for item in valid_documents:
            label = DOCUMENT_LABELS.get(item["tipo_documento"], item["tipo_documento"].upper())
            lines.append(f"- {label} *{item['numero_documento']}*")

        return (
            f"Olá *{nome}*, tudo bem?\n\n"
            "Identificamos documentos aguardando sua assinatura eletrônica:\n\n"
            + "\n".join(lines)
            + "\n\n"
            "Por favor, verifique seu e-mail (remetente: *Arkia Arklok*) e assine os documentos "
            "para finalizarmos seu atendimento.\n\n"
            "A assinatura é importante para o fechamento do chamado e para não comprometer nosso SLA.\n\n"
            "Se já assinou todos, desconsidere esta mensagem.\n\n"
            "Atenciosamente,\n"
            "*Equipe Arklok*\n\n"
            "---\n"
            "Esta é uma mensagem automática. Não responda este número."
        )

    def reminder_followup(
        self,
        *,
        nome: str,
        tipo_documento: str,
        numero_documento: str,
        dias_pendente: int,
    ) -> str:
        label = DOCUMENT_LABELS.get(tipo_documento, tipo_documento.upper())
        return (
            f"Olá {nome},\n\n"
            f"Já se passaram {dias_pendente} dias desde que enviamos seu {label} *{numero_documento}* "
            "para assinatura.\n\n"
            "Seu documento ainda está pendente. Isso pode impactar o fechamento do chamado e nosso SLA.\n\n"
            "Por favor, verifique seu e-mail (remetente: *Arkia Arklok*) e assine o documento o mais breve possível.\n\n"
            "Caso não tenha recebido o e-mail, entre em contato com nossa central.\n\n"
            "Atenciosamente,\n"
            "*Equipe Arklok*\n\n"
            "---\n"
            "Esta é uma mensagem automática. Não responda este número."
        )

    def signed_success(self, *, nome: str, tipo_documento: str, numero_documento: str) -> str:
        label = DOCUMENT_LABELS.get(tipo_documento, tipo_documento.upper())
        return (
            f"Olá {nome},\n\n"
            f"Seu {label} *{numero_documento}* foi assinado com sucesso.\n\n"
            "Seu atendimento foi concluído e o chamado será encerrado.\n\n"
            "Agradecemos pela colaboração.\n\n"
            "Atenciosamente,\n"
            "*Equipe Arklok*"
        )

    def return_notice(self, *, nome: str, equipamento: str) -> str:
        return (
            f"Olá {nome},\n\n"
            "Estamos aguardando a devolução do equipamento:\n\n"
            f"{equipamento}\n\n"
            "Por favor, entre em contato com nossa central para agendar a devolução.\n\n"
            "Atenciosamente,\n"
            "*Equipe Arklok*"
        )

    def custom(self, *, nome: str, texto: str) -> str:
        return f"Olá {nome},\n\n{texto}\n\nAtenciosamente,\n*Equipe Arklok*"
