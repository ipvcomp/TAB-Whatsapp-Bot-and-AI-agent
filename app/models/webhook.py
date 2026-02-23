from typing import Optional, Any
from pydantic import BaseModel, Field


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


class WhatsAppImage(BaseModel):
    caption: Optional[str] = None
    mime_type: str
    sha256: str
    id: str


class WhatsAppAudio(BaseModel):
    mime_type: str
    sha256: str
    id: str
    voice: Optional[bool] = None


class WhatsAppVideo(BaseModel):
    caption: Optional[str] = None
    mime_type: str
    sha256: str
    id: str


class WhatsAppDocument(BaseModel):
    caption: Optional[str] = None
    filename: Optional[str] = None
    mime_type: str
    sha256: str
    id: str


class WhatsAppLocation(BaseModel):
    latitude: float
    longitude: float
    name: Optional[str] = None
    address: Optional[str] = None


class WhatsAppReaction(BaseModel):
    message_id: str
    emoji: str


class WhatsAppSticker(BaseModel):
    mime_type: str
    sha256: str
    id: str
    animated: Optional[bool] = None


class WhatsAppContext(BaseModel):
    from_: Optional[str] = Field(None, alias="from")
    id: Optional[str] = None
    referred_product: Optional[dict] = None

    model_config = {"populate_by_name": True}


class WhatsAppInteractiveReply(BaseModel):
    id: str
    title: str


class WhatsAppInteractive(BaseModel):
    type: str
    button_reply: Optional[WhatsAppInteractiveReply] = None
    list_reply: Optional[WhatsAppInteractiveReply] = None


class WhatsAppButton(BaseModel):
    payload: str
    text: str


class WhatsAppMessage(BaseModel):
    sender: Optional[str] = Field(None, alias="from")
    id: str
    timestamp: str
    type: str
    text: Optional[WhatsAppText] = None
    image: Optional[WhatsAppImage] = None
    audio: Optional[WhatsAppAudio] = None
    video: Optional[WhatsAppVideo] = None
    document: Optional[WhatsAppDocument] = None
    location: Optional[WhatsAppLocation] = None
    reaction: Optional[WhatsAppReaction] = None
    sticker: Optional[WhatsAppSticker] = None
    interactive: Optional[WhatsAppInteractive] = None
    button: Optional[WhatsAppButton] = None
    context: Optional[WhatsAppContext] = None
    errors: Optional[list[dict]] = None

    model_config = {"populate_by_name": True}


class WhatsAppPricing(BaseModel):
    billable: Optional[bool] = None
    pricing_model: Optional[str] = None
    category: Optional[str] = None


class WhatsAppConversation(BaseModel):
    id: str
    origin: Optional[dict] = None
    expiration_timestamp: Optional[str] = None


class WhatsAppStatus(BaseModel):
    id: str
    status: str
    timestamp: str
    recipient_id: str
    conversation: Optional[WhatsAppConversation] = None
    pricing: Optional[WhatsAppPricing] = None
    errors: Optional[list[dict]] = None


class WhatsAppMetadata(BaseModel):
    display_phone_number: str
    phone_number_id: str


class WhatsAppValue(BaseModel):
    messaging_product: str
    metadata: WhatsAppMetadata
    contacts: Optional[list[WhatsAppContact]] = None
    messages: Optional[list[WhatsAppMessage]] = None
    statuses: Optional[list[WhatsAppStatus]] = None
    errors: Optional[list[dict]] = None


class WhatsAppChange(BaseModel):
    value: WhatsAppValue
    field: str


class WhatsAppEntry(BaseModel):
    id: str
    changes: list[WhatsAppChange]


class WebhookPayload(BaseModel):
    object: str
    entry: list[WhatsAppEntry]
