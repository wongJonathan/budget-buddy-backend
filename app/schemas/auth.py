from pydantic import BaseModel, SecretStr


class LoginRequest(BaseModel):
    email: str
    password: SecretStr
