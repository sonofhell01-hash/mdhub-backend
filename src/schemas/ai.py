from typing import Literal

from pydantic import BaseModel, Field


class ReviewContext(BaseModel):
    document_type: str = Field(default="laudo", examples=["laudo"])
    usage_inappropriate_selected: bool = False


class ReviewReportRequest(BaseModel):
    text: str = Field(examples=["Equipamento verificado e sem falhas durante os testes realizados."])
    context: ReviewContext = Field(default_factory=ReviewContext)


class SummarizeRequest(BaseModel):
    text: str = Field(examples=["Equipamento recebido para analise. Foram realizados testes de inicializacao e conectividade, sem falhas no momento."])
    purpose: Literal["operational_summary"] = "operational_summary"


class ReplacementScriptRequest(BaseModel):
    replacement_type: str = Field(examples=["troca de notebook"])
    known_facts: list[str] = Field(
        min_length=1,
        max_length=30,
        examples=[["Equipamento anterior recolhido", "Novo equipamento entregue", "Testes realizados com sucesso"]],
    )


class AIResponse(BaseModel):
    request_id: str
    suggestion: str
    warnings: list[str]
    model: str
    prompt_version: str
    elapsed_ms: int


class AIHealthResponse(BaseModel):
    enabled: bool
    pilot: bool
    allowed: bool
    provider: str
    reachable: bool
    model: str
    model_available: bool
    vision_model: str
    vision_model_available: bool


class LaudoImageInput(BaseModel):
    name: str = Field(min_length=1, max_length=220)
    data_url: str = Field(min_length=20)


class AnalyzeLaudoImagesRequest(BaseModel):
    images: list[LaudoImageInput] = Field(min_length=1, max_length=6)
    template_label: str = Field(default="", max_length=140)
    technician_observation: str = Field(default="", max_length=2000)


class LaudoVisualAnalysis(BaseModel):
    visible_damage: list[str]
    damage_location: list[str]
    possible_misuse_indicators: list[str]
    limitations: list[str]
    suggested_actions: str
    suggested_defect: str
    suggested_analysis: str
    suggested_solution: str


class LaudoVisualAnalysisResponse(BaseModel):
    request_id: str
    analysis: LaudoVisualAnalysis
    warnings: list[str]
    model: str
    prompt_version: str
    elapsed_ms: int
