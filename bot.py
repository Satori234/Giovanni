import asyncio
import os
import random

import discord

# ================================================================
# CONFIGURAZIONE
# ================================================================
# Modificato con il nuovo ID fornito
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "1530923578810306704"))
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN mancante")

# ================================================================
# UTENTI
# ================================================================
RAID_DM_AUTHOR_ID = 571027211407196161

# ================================================================
# TIMEOUT E RITARDI
# ================================================================
ACTION_WINDOW = 7.0
RD_BT_RESPONSE_TIMEOUT = ACTION_WINDOW

RAID_DM_DELAY_MIN = 3.0
RAID_DM_DELAY_MAX = 6.0
INTERACTION_RANDOM_MIN = 1.0
INTERACTION_RANDOM_MAX = 3.0

# ================================================================
# COMPONENTI
# ================================================================
RAID_ACCEPT_LABEL = "Accept"


# ================================================================
# UTILITY
# ================================================================
def iter_components(message):
    """Itera ricorsivamente su tutti i componenti del messaggio."""
    stack = list(getattr(message, "components", ()) or ())
    visited = set()

    while stack:
        component = stack.pop(0)
        identity = id(component)
        if identity in visited:
            continue
        visited.add(identity)

        yield component

        for attr in ("children", "components"):
            nested = getattr(component, attr, None)
            if nested:
                stack.extend(nested)

        accessory = getattr(component, "accessory", None)
        if accessory is not None:
            stack.append(accessory)

        nested_component = getattr(component, "component", None)
        if nested_component is not None:
            stack.append(nested_component)


def iter_buttons(message):
    """Itera ricorsivamente sui pulsanti cliccabili del messaggio."""
    for component in iter_components(message):
        if callable(getattr(component, "click", None)):
            yield component


def message_text(message) -> str:
    """Estrae testo da content, embed e Components V2."""
    parts = []

    content = getattr(message, "content", None)
    if isinstance(content, str):
        parts.append(content)

    for embed in getattr(message, "embeds", ()):
        for value in (
            getattr(embed, "title", None),
            getattr(embed, "description", None),
        ):
            if isinstance(value, str):
                parts.append(value)

        footer = getattr(embed, "footer", None)
        footer_text = getattr(footer, "text", None) if footer is not None else None
        if isinstance(footer_text, str):
            parts.append(footer_text)

        for field in getattr(embed, "fields", ()):
            for value in (
                getattr(field, "name", None),
                getattr(field, "value", None),
            ):
                if isinstance(value, str):
                    parts.append(value)

    for component in iter_components(message):
        for attr in ("content", "text"):
            value = getattr(component, attr, None)
            if isinstance(value, str):
                parts.append(value)

    return "\n".join(parts).casefold()


def is_anigame_dm(message) -> bool:
    """Verifica che il messaggio arrivi in DM dall'ID specificato."""
    return (
        message.author.id == RAID_DM_AUTHOR_ID
        and getattr(message, "guild", None) is None
    )


def raid_dm_trigger(message) -> bool:
    """Riconosce i DM specifici di 'energy reminder'."""
    if not is_anigame_dm(message):
        return False

    text = message_text(message)

    return (
        "energy reminder" in text
        and "raid energy is almost fully restored" in text
    )


def first_button_with_label(message, expected_label: str):
    expected = expected_label.casefold()

    return next(
        (
            button
            for button in iter_buttons(message)
            if isinstance(getattr(button, "label", None), str)
            and button.label.casefold() == expected
        ),
        None,
    )


def first_accept_button(message):
    return first_button_with_label(message, RAID_ACCEPT_LABEL)


async def delay_inside_window(deadline: float) -> bool:
    """
    Applica il ritardo casuale senza oltrepassare la deadline originale.
    """
    loop = asyncio.get_running_loop()
    wait = random.uniform(INTERACTION_RANDOM_MIN, INTERACTION_RANDOM_MAX)

    if loop.time() + wait > deadline:
        return False

    await asyncio.sleep(wait)
    return loop.time() <= deadline


async def component_action(button) -> bool:
    try:
        await button.click()
        return True
    except Exception:
        return False


# ================================================================
# CLIENT
# ================================================================
class ScheduledBot(discord.Client):
    async def setup_hook(self):
        self.rd_bt_deadline = 0.0
        self.rd_bt_processing = False
        self._last_raid_dm_trigger_id = None
        self._target_channel = None

    # ============================================================
    # CANALE
    # ============================================================
    async def target_channel(self):
        if self._target_channel is not None:
            return self._target_channel

        channel = self.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await self.fetch_channel(CHANNEL_ID)

        self._target_channel = channel
        return channel

    # ============================================================
    # INVIO
    # ============================================================
    async def send(self, channel, text: str) -> bool:
        try:
            await channel.send(text)
            return True
        except (discord.Forbidden, discord.NotFound):
            await self.close()
            return False
        except discord.HTTPException:
            return False

    # ============================================================
    # INTERCETTAZIONE MESSAGGI
    # ============================================================
    async def on_message(self, message):
        # I trigger DM vanno gestiti prima del filtro CHANNEL_ID.
        if await self.handle_raid_dm_trigger(message):
            return

        if message.channel.id != CHANNEL_ID:
            return

        await self.handle_rd_bt_confirmation(message)

    async def on_message_edit(self, before, after):
        # Copre anche trigger DM costruiti/aggiornati via MESSAGE_UPDATE.
        if await self.handle_raid_dm_trigger(after):
            return

        if after.channel.id != CHANNEL_ID:
            return

        await self.handle_rd_bt_confirmation(after)

    # ============================================================
    # RAID TRIGGER DM
    # ============================================================
    async def handle_raid_dm_trigger(self, message) -> bool:
        if not raid_dm_trigger(message):
            return False

        if message.id == self._last_raid_dm_trigger_id:
            return True

        self._last_raid_dm_trigger_id = message.id

        await asyncio.sleep(
            random.uniform(
                RAID_DM_DELAY_MIN,
                RAID_DM_DELAY_MAX,
            )
        )

        channel = await self.target_channel()
        await self.handle_rd_bt_all(channel)
        return True

    # ============================================================
    # .rd bt all + CONFERMA RAID
    # ============================================================
    async def handle_rd_bt_all(self, channel):
        loop = asyncio.get_running_loop()

        self.rd_bt_deadline = loop.time() + RD_BT_RESPONSE_TIMEOUT

        success = await self.send(channel, ".rd bt all")
        if not success:
            self.rd_bt_deadline = 0.0

    async def handle_rd_bt_confirmation(self, message):
        loop = asyncio.get_running_loop()
        deadline = self.rd_bt_deadline

        if (
            deadline <= 0.0
            or loop.time() > deadline
            or self.rd_bt_processing
        ):
            return

        button = first_accept_button(message)
        if button is None:
            return

        self.rd_bt_processing = True

        try:
            if not await delay_inside_window(deadline):
                return

            if loop.time() > deadline:
                return

            success = await component_action(button)

            if not success and loop.time() <= deadline:
                try:
                    fresh_message = await message.channel.fetch_message(message.id)
                except (
                    discord.Forbidden,
                    discord.NotFound,
                    discord.HTTPException,
                ):
                    pass
                else:
                    fresh_button = first_accept_button(fresh_message)
                    if fresh_button is not None and loop.time() <= deadline:
                        success = await component_action(fresh_button)

            if success:
                self.rd_bt_deadline = 0.0
        finally:
            self.rd_bt_processing = False


# ================================================================
# AVVIO
# ================================================================
if __name__ == "__main__":
    client = ScheduledBot(
        max_messages=200,
        member_cache_flags=discord.MemberCacheFlags.none(),
        chunk_guilds_at_startup=False,
    )
    client.run(TOKEN)