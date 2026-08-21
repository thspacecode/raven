from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.utils import get_host_name, get_site_name, get_url

from raven.omni_channel_chat.doctype.omni_channel_chat_provider.omni_channel_chat_provider import (
	get_omni_channel_chat_provider,
)
from raven.omni_channel_chat.models.message import (
	ChatDestination,
	FileContent,
	FileMessage,
	FileUrl,
	ImageMessage,
	RavenUserId,
	StdMessage,
	TextMessage,
	UserDisplay,
)

if TYPE_CHECKING:
	from frappe.core.doctype.user.user import User

	from raven.omni_channel_chat.doctype.omni_channel_chat_provider.provider import Provider
	from raven.raven.doctype.raven_user.raven_user import RavenUser
	from raven.raven_channel_management.doctype.raven_channel.raven_channel import RavenChannel
	from raven.raven_messaging.doctype.raven_message.raven_message import RavenMessage


class OmniChannelRavenConnector:
	provider_prefix = "occ"

	def __init__(self, provider: "Provider"):
		self.provider = provider

	@staticmethod
	def get_provider_from_channel(channel_name: str) -> "Provider":
		provider_name = frappe.db.get_value(
			doctype="Raven Channel",
			filters=channel_name,
			fieldname="omni_channel_chat_provider",
		)
		if not provider_name:
			frappe.throw(_("Omni Channel Chat Provider not found for channel {0}").format(channel_name))
		return get_omni_channel_chat_provider(
			slug=provider_name,
		)

	def get_provider_pk(self, provider_id: str) -> str:
		return f"{self.provider_prefix}_{provider_id}"

	def get_channel_name(self, destination_id: str) -> str:
		return f"{self.provider.provider_config.name}_{destination_id}"

	def get_sender_info(self, sender: RavenUserId) -> "UserDisplay | None":
		raven_user = frappe.db.get_value(
			"Raven User", sender.user_id, ["full_name", "user_image"], as_dict=True
		)
		if not raven_user:
			return None
		icon_url = raven_user["user_image"]
		if icon_url and icon_url.startswith("/"):
			icon_url = get_url(icon_url)
		return UserDisplay(name=raven_user["full_name"], icon_url=icon_url)

	def get_user_id(self, channel_id: str) -> str:
		"""Return the provider user_id for a 1:1 customer channel via social login lookup."""
		channel_user = frappe.db.get_value(
			doctype="Raven Channel",
			filters=channel_id,
			fieldname="customer_user",
		)

		user_id = frappe.db.get_value(
			doctype="User Social Login",
			filters={
				"provider": self.get_provider_pk(self.provider.provider_config.name),
				"parent": channel_user,
			},
			fieldname="userid",
		)

		if not user_id:
			frappe.throw(_("User ID not found for channel {0}").format(channel_id))

		return user_id

	def get_destination_id(self, channel_id: str) -> str | None:
		"""Return the provider destination_id (user_id or group_id) for outbound routing."""
		destination_id = frappe.db.get_value(
			doctype="Raven Channel",
			filters=channel_id,
			fieldname="omni_channel_destination_id",
		)
		return destination_id

	def raven_to_std_msg(
		self,
		raven_message: "RavenMessage",
		sender: "UserDisplay | None",
	) -> StdMessage:
		msg_type = raven_message.message_type
		if msg_type == "Text":
			return TextMessage(
				text=raven_message.content or "",
				sender=sender,
			)
		file_url = raven_message.file
		if file_url and file_url.startswith("/"):
			file_url = get_url(file_url)
		if msg_type == "Image":
			return ImageMessage(
				file=FileUrl(url=file_url),
				sender=sender,
			)
		if msg_type == "File":
			return FileMessage(
				file=FileUrl(url=file_url),
				sender=sender,
			)

		raise ValueError(f"Unsupported outbound message type: {msg_type}")

	def get_hostname_without_port(self) -> str:
		return get_site_name(get_host_name())

	def create_customer_user(self, user_id: str, provider_id: str) -> "User":
		provider_pk = self.get_provider_pk(provider_id)

		username = frappe.generate_hash(length=10)
		hostname = self.get_hostname_without_port()

		email = f"{username}@{self.provider_prefix}.{hostname}"

		user = frappe.new_doc("User")
		user.update(
			{
				"email": email,
				"username": username,
				"first_name": username,
				"enabled": 1,
				"user_type": "Website User",
			}
		)
		user.insert(ignore_permissions=True)

		user_social_login = frappe.new_doc("User Social Login")
		user_social_login.update(
			{
				"provider": provider_pk,
				"userid": user_id,
				"parent": user.name,
				"parenttype": "User",
				"parentfield": "social_logins",
			}
		)
		user_social_login.insert(ignore_permissions=True)

		return user

	def get_or_create_customer_user(self, user_id: str | None, provider_id: str) -> "User":
		provider_pk = self.get_provider_pk(provider_id)

		user_pk = frappe.db.get_value(
			doctype="User Social Login",
			filters={"provider": provider_pk, "userid": user_id},
			fieldname="parent",
		)

		if user_pk:
			return frappe.get_doc("User", user_pk)

		return self.create_customer_user(user_id=user_id, provider_id=provider_id)

	def create_raven_user(
		self, user: "User", user_id: str, destination: "ChatDestination | None" = None
	) -> "RavenUser":
		user_info = self.provider.get_user_info(user_id=user_id, destination=destination)

		raven_user = frappe.new_doc("Raven User")
		raven_user.update(
			{
				"type": "Customer",
				"user": user.name,
				"full_name": user_info.name,
				"user_image": user_info.icon_url,
				"enabled": True,
			}
		)
		raven_user.insert(ignore_permissions=True)

		return raven_user

	def get_or_create_raven_user(
		self, user: "User", user_id: str, destination: "ChatDestination | None" = None
	) -> "RavenUser":
		raven_user_pk = frappe.db.get_value(
			doctype="Raven User",
			filters={"user": user.name},
			fieldname="name",
		)

		if raven_user_pk:
			return frappe.get_doc("Raven User", raven_user_pk)

		return self.create_raven_user(user=user, user_id=user_id, destination=destination)

	def create_channel(
		self, destination: ChatDestination, raven_user: "RavenUser | None" = None
	) -> "RavenChannel":
		channel_name = self.get_channel_name(
			destination_id=destination.destination_id,
		)

		omni_channel_raven_user = None
		if raven_user and destination.type == "User":
			omni_channel_raven_user = raven_user.name

		channel = frappe.new_doc("Raven Channel")
		channel.update(
			{
				"channel_name": channel_name,
				"id": channel_name,
				"type": "Public",
				"omni_channel_chat_provider": self.provider.provider_config.name,
				"omni_channel_destination_id": destination.destination_id,
				"omni_channel_raven_user": omni_channel_raven_user,
				"is_customer": True,
				"enabled": True,
				"workspace": self.provider.provider_config.raven_workspace,
			}
		)
		channel.insert(ignore_permissions=True)

		channel_display = self.provider.get_destination_display_name(destination=destination)
		channel.update(
			{
				"channel_name": channel_display.name,
			}
		)
		channel.save(ignore_permissions=True)

		return channel

	def get_or_create_channel(
		self, destination: ChatDestination, raven_user: "RavenUser | None" = None
	) -> "RavenChannel":
		channel_name = self.get_channel_name(
			destination_id=destination.destination_id,
		)

		channel_pk = frappe.db.get_value(
			doctype="Raven Channel",
			filters=channel_name,
			fieldname="name",
		)

		if channel_pk:
			return frappe.get_doc("Raven Channel", channel_pk)

		return self.create_channel(destination=destination, raven_user=raven_user)

	def create_raven_message(
		self,
		raven_channel: "RavenChannel",
		message: StdMessage,
		sender_user: "User | None" = None,
		provider_metadata: dict | None = None,
		raven_user: "RavenUserId | None" = None,
		raven_message_data: dict | None = None,
	) -> None:
		doc = frappe.new_doc(doctype="Raven Message")
		doc.update(
			{
				"channel_id": raven_channel.name,
				"message_type": message.type,
				"is_customer_message": True,
				"omni_channel_msg_meta": provider_metadata,
			}
		)

		if raven_user is not None:
			if raven_user.user_type == "Raven User":
				doc.update(
					{
						"owner": raven_user.user_id,
					}
				)
			elif raven_user.user_type == "Raven Bot":
				doc.update(
					{
						"is_bot_message": True,
						"bot": raven_user.user_id,
					}
				)

		if isinstance(message, TextMessage):
			doc.text = message.text
		elif isinstance(message, (ImageMessage, FileMessage)) and isinstance(message.file, FileContent):
			file_doc = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": message.file.file_name,
					"content": message.file.file_content,
					"is_private": 0,
				}
			)
			file_doc.insert(ignore_permissions=True)
			doc.file = file_doc.file_url

		doc.update(raven_message_data or {})

		doc.insert(ignore_permissions=True)

	# ── Provider Message → Raven Message (inbound) ─────────────────────────────

	def handle_inbound(
		self,
		destination: ChatDestination,
		sender_id: str,
		std_message: StdMessage,
	) -> None:
		"""Inbound: turn a provider webhook payload into a Raven message.

		Creates the Frappe user, Raven user, and channel on first contact,
		then appends the message to the channel.
		"""
		user = self.get_or_create_customer_user(
			user_id=sender_id,
			provider_id=self.provider.provider_config.name,
		)

		raven_user = self.get_or_create_raven_user(
			user=user,
			user_id=sender_id,
			destination=destination,
		)

		frappe.set_user(user.name)

		raven_channel = self.get_or_create_channel(
			destination=destination,
			raven_user=raven_user,
		)

		provider_metadata = {
			"provider_user_id": sender_id,
			"destination_id": destination.destination_id,
			"destination_type": destination.type,
		}

		self.create_raven_message(
			raven_channel=raven_channel,
			message=std_message,
			sender_user=user,
			provider_metadata=provider_metadata,
		)

	# ── Raven Message → Provider Message (outbound) ────────────────────────────

	def handle_outbound(self, raven_message: "RavenMessage") -> None:
		"""Outbound: forward a staff Raven message to the customer on the external provider.

		Skips messages that are from customers, bots, or system/poll types,
		and channels that are not omni-channel customer channels.
		"""
		if (
			raven_message.is_customer_message
			or raven_message.is_bot_message
			or raven_message.message_type in ("System", "Poll")
			or raven_message.omni_channel_skip_push_to_provider
		):
			return

		channel = frappe.db.get_value(
			doctype="Raven Channel",
			filters=raven_message.channel_id,
			fieldname=["is_customer", "omni_channel_destination_id"],
			as_dict=True,
		)
		if not channel or not channel.is_customer:
			return

		destination_id = self.get_destination_id(raven_message.channel_id)
		sender = self.get_sender_info(
			sender=RavenUserId(user_type="Raven User", user_id=raven_message.owner)
		)
		outbound_msg = self.raven_to_std_msg(raven_message=raven_message, sender=sender)
		self.provider.send_message(destination_id=destination_id, message=outbound_msg)

	# ── Inject ─────────────────────────────────────────────────────────────────

	def handle_inject(
		self,
		destination: ChatDestination,
		std_message: StdMessage,
		raven_user: RavenUserId,
	) -> None:
		"""Handle programmatically injected messages, e.g. from a bot or workflow.

		Saves the message to the Raven channel. The after_insert hook on RavenMessage
		automatically calls handle_outbound which pushes the message to the provider.
		"""
		raven_channel = self.get_or_create_channel(
			destination=destination,
		)

		self.create_raven_message(
			raven_channel=raven_channel,
			message=std_message,
			raven_user=raven_user,
			raven_message_data={"omni_channel_skip_push_to_provider": True},
		)

		sender_info = self.get_sender_info(sender=raven_user)
		std_message.sender = sender_info

		self.provider.send_message(
			destination_id=destination.destination_id,
			message=std_message,
		)
