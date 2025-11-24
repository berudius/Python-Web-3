from pydantic import BaseModel
from typing import Optional

class UserUpdatePayload(BaseModel):
    login: Optional[str] = None
    phone_number: Optional[str] = None
    role: Optional[str] = None
    
    # Додаємо поля для ігрової механіки
    trust_level: Optional[int] = None
    consecutive_cancellations: Optional[int] = None

    class Config:
        orm_mode = True