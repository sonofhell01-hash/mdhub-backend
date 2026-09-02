class AIError(Exception):
    code = "ai_error"


class AIUnavailable(AIError):
    code = "unavailable"


class AIRequestTimeout(AIError):
    code = "timeout"


class AIBusy(AIError):
    code = "busy"


class AIInvalidResponse(AIError):
    code = "invalid_response"


class AIInputRejected(AIError):
    code = "input_rejected"


class AIInputTooLarge(AIInputRejected):
    code = "input_too_large"
