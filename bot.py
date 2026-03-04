import asyncio
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

DB_PATH = "notes.db"
router = Router()


class NoteFSM(StatesGroup):
    waiting_note = State()
    waiting_book = State()
    waiting_category = State()


def get_bot_token() -> str:
    token_keys = ("BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TOKEN")
    for key in token_keys:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    raise RuntimeError(
        "Set one of environment variables: BOT_TOKEN, TELEGRAM_BOT_TOKEN, TOKEN"
    )


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                book TEXT NOT NULL,
                category TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def save_note(text: str, book: str, category: str) -> None:
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO notes (text, book, category, created_at) VALUES (?, ?, ?, ?)",
            (text, book, category, created_at),
        )
        conn.commit()


def get_books() -> list[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT DISTINCT book FROM notes ORDER BY book COLLATE NOCASE"
        ).fetchall()
    return [row[0] for row in rows]


def get_categories() -> list[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM notes ORDER BY category COLLATE NOCASE"
        ).fetchall()
    return [row[0] for row in rows]


def find_notes(query: str) -> list[tuple[str, str, str, str]]:
    pattern = f"%{query.strip()}%"
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT text, book, category, created_at
            FROM notes
            WHERE book LIKE ? COLLATE NOCASE OR category LIKE ? COLLATE NOCASE
            ORDER BY id DESC
            """,
            (pattern, pattern),
        ).fetchall()
    return rows


def get_random_note() -> tuple[str, str, str, str] | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT text, book, category, created_at FROM notes ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
    return row


def format_note(row: tuple[str, str, str, str]) -> str:
    text, book, category, created_at = row
    return (
        f"Текст: {text}\n"
        f"Книга: {book}\n"
        f"Категория: {category}\n"
        f"Дата: {created_at}"
    )


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.set_state(NoteFSM.waiting_note)
    await message.answer(
        "Отправь мысль/заметку из книги. Я сохраню ее как содержание карточки."
    )


@router.message(Command("library"))
async def cmd_library(message: Message) -> None:
    books = get_books()
    if not books:
        await message.answer("Пока нет заметок по книгам.")
        return
    response = "Книги:\n" + "\n".join(f"- {book}" for book in books)
    await message.answer(response)


@router.message(Command("categories"))
async def cmd_categories(message: Message) -> None:
    categories = get_categories()
    if not categories:
        await message.answer("Пока нет тем.")
        return
    response = "Темы:\n" + "\n".join(f"- {category}" for category in categories)
    await message.answer(response)


@router.message(Command("view"))
async def cmd_view(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Использование: /view [название книги или категория]")
        return

    rows = find_notes(command.args)
    if not rows:
        await message.answer("Ничего не найдено.")
        return

    # Ограничиваем вывод, чтобы не упереться в лимит длины сообщения Telegram.
    max_items = 10
    preview = rows[:max_items]
    response = "\n\n".join(format_note(row) for row in preview)
    if len(rows) > max_items:
        response += f"\n\nПоказано {max_items} из {len(rows)} заметок."
    await message.answer(response)


@router.message(Command("random"))
async def cmd_random(message: Message) -> None:
    row = get_random_note()
    if not row:
        await message.answer("База пуста. Сначала сохрани хотя бы одну карточку.")
        return
    await message.answer("Случайная карточка:\n\n" + format_note(row))


async def handle_note_step(message: Message, state: FSMContext) -> None:
    await state.update_data(text=message.text.strip())
    await state.set_state(NoteFSM.waiting_book)
    await message.answer("Из какой это книги?")


@router.message(StateFilter(NoteFSM.waiting_note), F.text)
async def on_waiting_note(message: Message, state: FSMContext) -> None:
    await handle_note_step(message, state)


@router.message(StateFilter(None), F.text)
async def on_text_without_state(message: Message, state: FSMContext) -> None:
    # Любой текст вне FSM считаем первым шагом карточки.
    await handle_note_step(message, state)


@router.message(StateFilter(NoteFSM.waiting_book), F.text)
async def on_waiting_book(message: Message, state: FSMContext) -> None:
    await state.update_data(book=message.text.strip())
    await state.set_state(NoteFSM.waiting_category)
    await message.answer("К какой категории/теме относится эта мысль?")


@router.message(StateFilter(NoteFSM.waiting_category), F.text)
async def on_waiting_category(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = data.get("text")
    book = data.get("book")
    category = message.text.strip()

    if not text or not book:
        await state.clear()
        await message.answer("Сессия сброшена. Отправь заметку заново.")
        return

    save_note(text=text, book=book, category=category)
    await state.clear()
    await message.answer("Карточка сохранена!")


async def main() -> None:
    init_db()
    bot = Bot(token=get_bot_token())
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
