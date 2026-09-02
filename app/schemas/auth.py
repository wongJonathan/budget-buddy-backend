from pydantic import BaseModel, SecretStr

from app.schemas.fields import NormalizedEmail


class LoginRequest(BaseModel):
    email: NormalizedEmail
    password: SecretStr
