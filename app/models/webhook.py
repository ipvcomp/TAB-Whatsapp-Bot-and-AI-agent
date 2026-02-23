from typing import Optional
from pydantic import BaseModel


class WebhookVerification(BaseModel):
    hub_mode: str
    hub_verify_token: str
    hub_challenge: str


class WhatsAppProfile(BaseModel):
    name: str


class WhatsAppContact(BaseModel):
    profile: WhatsAppProfile
    wa_id: str


class WhatsAppText(BaseModel):
    body: str


class WhatsAppMessage(BaseModel):
    from_: Optional[str] = None
    id: str
    timestamp: str
    text: Optional[WhatsAppText] = None
    type: str

    class Config:
        populate_by_name = True


class WhatsAppStatus(BaseModel):
    id: str
    status: str
    timestamp: str
    recipient_id: str


class WhatsAppValue(BaseModel):
    messaging_product: str
    metadata: dict
    contacts: Optional[list[WhatsAppContact]] = None
    messages: Optional[list[WhatsAppMessage]] = None
    statuses: Optional[list[WhatsAppStatus]] = None


class WhatsAppChange(BaseModel):
    value: WhatsAppValue
    field: str


class WhatsAppEntry(BaseModel):
    id: str
    changes: list[WhatsAppChange]


class WebhookPayload(BaseModel):
    object: str
    entry: list[WhatsAppEntry]
