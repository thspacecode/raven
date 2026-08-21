from typing import Callable

import frappe
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
	ApiClient,
	Configuration,
	MessagingApi,
	MessagingApiBlob,
	PushMessageRequest,
	ShowLoadingAnimationRequest,
)
from linebot.v3.messaging.exceptions import NotFoundException
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import Event as LineEvent
from linebot.v3.webhooks import FileMessageContent, GroupSource, ImageMessageContent
from linebot.v3.webhooks import MessageEvent as LineMessageEvent
from linebot.v3.webhooks import RoomSource, TextMessageContent
from werkzeug.wrappers import Response

from raven.omni_channel_chat.models.message import (
	ChatDestination,
	FileContent,
	FileMessage,
	ImageMessage,
	StdInboundEvent,
	StdMessage,
)
from raven.omni_channel_chat.models.message import TextMessage as StdTextMessage
from raven.omni_channel_chat.models.message import UserDisplay

from . import Provider


class LineProvider(Provider[LineEvent]):
	config: Configuration
	parser: WebhookParser

	def __init__(self, config):
		super().__init__(config=config)

		self.config = Configuration(
			access_token=self.provider_config.line_channel_access_token,
		)
		self.parser = WebhookParser(
			channel_secret=self.provider_config.line_channel_secret,
		)

	def handle_frappe_api(
		self, callback: Callable[[ChatDestination, str, StdMessage], None]
	) -> Response:
		request = frappe.local.request
		body: bytes = request.get_data()
		headers: dict = dict(request.headers)
		return self.handle_webhook(body=body, headers=headers, callback=callback)

	def get_destination_display_name(self, destination: "ChatDestination") -> UserDisplay:
		if destination.type != "Group":
			return self.get_user_info(destination.destination_id, destination)
		with ApiClient(self.config) as api_client:
			summary = MessagingApi(api_client).get_group_summary(destination.destination_id)
			return UserDisplay(name=summary.group_name, icon_url=None)

	def get_user_info(
		self,
		user_id: str,
		destination: "ChatDestination",
	) -> UserDisplay:
		with ApiClient(self.config) as api_client:
			api = MessagingApi(api_client)
			if destination and destination.type == "Group":
				try:
					profile = api.get_group_member_profile(destination.destination_id, user_id)
					return UserDisplay(
						name=profile.display_name,
						icon_url=profile.picture_url,
					)
				except NotFoundException:
					return UserDisplay(name=user_id, icon_url=None)
			try:
				profile = api.get_profile(user_id)
				return UserDisplay(
					name=profile.display_name,
					icon_url=profile.picture_url,
				)
			except NotFoundException:
				return UserDisplay(name=user_id, icon_url=None)

	def show_typing(self, destination_id: str) -> None:
		with ApiClient(self.config) as api_client:
			MessagingApi(api_client).show_loading_animation(
				ShowLoadingAnimationRequest(chatId=destination_id, loadingSeconds=60)
			)

	def send_reply(self, destination_id: str, message: StdMessage) -> None:
		self.send_message(destination_id, message)

	def send_message(self, destination_id: str, message: StdMessage) -> None:
		line_msg = message.to_provider(provider_type=self.provider_config.provider)
		with ApiClient(self.config) as api_client:
			MessagingApi(api_client).push_message(
				PushMessageRequest(to=destination_id, messages=[line_msg])
			)

	def download_attachment(self, message_id: str, file_name: str | None = None) -> FileContent:
		with ApiClient(self.config) as api_client:
			content = bytes(MessagingApiBlob(api_client).get_message_content(message_id))
			return FileContent(file_name=file_name or "attachment", file_content=content)

	def event_mapper(self, event: LineEvent) -> StdInboundEvent | None:
		if not isinstance(event, LineMessageEvent):
			return None

		msg = event.message
		source = event.source

		if isinstance(source, GroupSource):
			destination = ChatDestination(type="Group", destination_id=source.group_id)
		elif isinstance(source, RoomSource):
			destination = ChatDestination(type="Group", destination_id=source.room_id)
		else:
			destination = ChatDestination(type="User", destination_id=source.user_id)

		provider_user = source.user_id

		if isinstance(msg, TextMessageContent):
			return StdInboundEvent(
				destination=destination,
				sender_id=provider_user,
				message=StdTextMessage(text=msg.text),
			)

		if isinstance(msg, ImageMessageContent):
			return StdInboundEvent(
				destination=destination,
				sender_id=provider_user,
				message=ImageMessage(file=self.download_attachment(msg.id, f"{msg.id}.jpg")),
			)
		if isinstance(msg, FileMessageContent):
			return StdInboundEvent(
				destination=destination,
				sender_id=provider_user,
				message=FileMessage(file=self.download_attachment(msg.id, msg.file_name)),
			)

		return None

	def standardize_events(self, events: list[LineEvent]) -> list[StdInboundEvent]:
		result: list[StdInboundEvent] = []
		for event in events:
			mapped = self.event_mapper(event)
			if mapped:
				result.append(mapped)
		return result

	def extract_messages(self, body: bytes, headers: dict) -> list[StdInboundEvent]:
		signature = headers.get("x-line-signature", "") or headers.get("X-Line-Signature", "")
		try:
			events = self.parser.parse(body=body.decode(), signature=signature, as_payload=False)
		except InvalidSignatureError:
			frappe.throw("Invalid LINE signature", frappe.PermissionError)
		return self.standardize_events(events)
