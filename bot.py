import asyncio
import os
import sqlite3
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

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
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                book TEXT NOT NULL,
                category TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        # Безопасная миграция для старой схемы таблицы без user_id.
        columns = conn.execute("PRAGMA table_info(notes)").fetchall()
        column_names = {column[1] for column in columns}
        if "user_id" not in column_names:
            conn.execute(
                "ALTER TABLE notes ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0"
            )
        conn.commit()


def save_note(user_id: int, text: str, book: str, category: str) -> None:
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO notes (user_id, text, book, category, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, text, book, category, created_at),
        )
        conn.commit()


def get_books(user_id: int) -> list[tuple[int, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT MIN(id) AS ref_id, book
            FROM notes
            WHERE user_id = ?
            GROUP BY book
            ORDER BY book COLLATE NOCASE
            """,
            (user_id,),
        ).fetchall()
    return [(row[0], row[1]) for row in rows]


def get_categories(user_id: int) -> list[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT category
            FROM notes
            WHERE user_id = ?
            ORDER BY category COLLATE NOCASE
            """,
            (user_id,),
        ).fetchall()
    return [row[0] for row in rows]


def find_notes(user_id: int, query: str) -> list[tuple[str, str, str, str]]:
    pattern = f"%{query.strip()}%"
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT text, book, category, created_at
            FROM notes
            WHERE user_id = ?
              AND (book LIKE ? COLLATE NOCASE OR category LIKE ? COLLATE NOCASE)
            ORDER BY id DESC
            """,
            (user_id, pattern, pattern),
        ).fetchall()
    return rows


def get_random_note(user_id: int) -> tuple[str, str, str, str] | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT text, book, category, created_at
            FROM notes
            WHERE user_id = ?
            ORDER BY RANDOM()
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return row


def format_note(row: tuple[str, str, str, str]) -> str:
    text, book, category, created_at = row
    return (
        f"Книга: {book}\n"
        f"Категория: {category}\n"
        f"Дата: {created_at}\n\n"
        f"Текст: {text}"
    )


def compact_label(text: str, max_len: int = 48) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def books_keyboard(books: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=compact_label(book), callback_data=f"book:{ref_id}")]
        for ref_id, book in books
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def add_flow_keyboard(books: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=compact_label(book), callback_data=f"newnote_book:{ref_id}"
            )
        ]
        for ref_id, book in books
    ]
    buttons.append(
        [InlineKeyboardButton(text="➕ Добавить", callback_data="newnote_add")]
    )
    buttons.append(
        [InlineKeyboardButton(text="📚 Мои заметки", callback_data="browse_books")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def open_notes_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📚 Мои заметки", callback_data="browse_books")]
        ]
    )


def categories_keyboard(
    categories: list[tuple[int, str]], book_ref_id: int
) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=compact_label(category), callback_data=f"cat:{ref_id}")]
        for ref_id, category in categories
    ]
    buttons.append(
        [InlineKeyboardButton(text="⬅️ К книгам", callback_data="browse_books")]
    )
    buttons.append(
        [InlineKeyboardButton(text="📚 Обновить категории", callback_data=f"book:{book_ref_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def notes_keyboard(book_ref_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К категориям", callback_data=f"book:{book_ref_id}")],
            [InlineKeyboardButton(text="📚 К книгам", callback_data="browse_books")],
        ]
    )


def get_book_by_ref(user_id: int, ref_id: int) -> Optional[str]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT book FROM notes WHERE id = ? AND user_id = ?",
            (ref_id, user_id),
        ).fetchone()
    return row[0] if row else None


def get_categories_by_book(
    user_id: int, book: str
) -> list[tuple[int, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT MIN(id) AS ref_id, category
            FROM notes
            WHERE user_id = ? AND book = ?
            GROUP BY category
            ORDER BY category COLLATE NOCASE
            """,
            (user_id, book),
        ).fetchall()
    return [(row[0], row[1]) for row in rows]


def get_note_meta_by_ref(user_id: int, ref_id: int) -> Optional[tuple[str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT book, category FROM notes WHERE id = ? AND user_id = ?",
            (ref_id, user_id),
        ).fetchone()
    return (row[0], row[1]) if row else None


def get_book_ref_id(user_id: int, book: str) -> Optional[int]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT MIN(id)
            FROM notes
            WHERE user_id = ? AND book = ?
            """,
            (user_id, book),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def get_notes_by_book_category(
    user_id: int, book: str, category: str
) -> list[tuple[str, str, str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT text, book, category, created_at
            FROM notes
            WHERE user_id = ? AND book = ? AND category = ?
            ORDER BY id DESC
            """,
            (user_id, book, category),
        ).fetchall()
    return rows


def format_notes_list(rows: list[tuple[str, str, str, str]], max_items: int = 10) -> str:
    preview = rows[:max_items]
    blocks = []
    for idx, row in enumerate(preview, start=1):
        text, _, _, created_at = row
        blocks.append(f"{idx}. Дата: {created_at}\n\n   Текст: {text}")
    result = "\n\n".join(blocks)
    if len(rows) > max_items:
        result += f"\n\nПоказано {max_items} из {len(rows)} заметок."
    return result


async def safe_edit_message(
    message: Message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        raise


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    books = get_books(user_id)
    await message.answer(
        "Выбери книгу для новой карточки или нажми «Добавить».",
        reply_markup=add_flow_keyboard(books),
    )


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    await cmd_start(message, state)


@router.message(Command("library"))
async def cmd_library(message: Message) -> None:
    user_id = message.from_user.id
    books = get_books(user_id)
    if not books:
        await message.answer("Пока нет заметок по книгам.")
        return
    await message.answer("Выбери книгу:", reply_markup=books_keyboard(books))


@router.message(Command("notes"))
async def cmd_notes(message: Message) -> None:
    await message.answer("Открой список заметок:", reply_markup=open_notes_keyboard())


@router.message(Command("categories"))
async def cmd_categories(message: Message) -> None:
    user_id = message.from_user.id
    categories = get_categories(user_id)
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

    user_id = message.from_user.id
    rows = find_notes(user_id, command.args)
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
    user_id = message.from_user.id
    row = get_random_note(user_id)
    if not row:
        await message.answer("База пуста. Сначала сохрани хотя бы одну карточку.")
        return
    await message.answer("Случайная карточка:\n\n" + format_note(row))


@router.message(StateFilter(NoteFSM.waiting_book), F.text)
async def on_waiting_book(message: Message, state: FSMContext) -> None:
    await state.update_data(book=message.text.strip())
    await state.set_state(NoteFSM.waiting_note)
    await message.answer("Отправь текст заметки.")


@router.message(StateFilter(NoteFSM.waiting_note), F.text)
async def on_waiting_note(message: Message, state: FSMContext) -> None:
    await state.update_data(text=message.text.strip())
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

    user_id = message.from_user.id
    save_note(user_id=user_id, text=text, book=book, category=category)
    await state.clear()
    await message.answer("Карточка сохранена!", reply_markup=open_notes_keyboard())


@router.message(StateFilter(None), F.text)
async def on_text_without_state(message: Message, state: FSMContext) -> None:
    await cmd_start(message, state)


@router.callback_query(F.data == "newnote_add")
async def on_newnote_add(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    await state.clear()
    await state.set_state(NoteFSM.waiting_book)
    await callback.answer()
    await callback.message.answer("Напиши название книги.")


@router.callback_query(F.data.startswith("newnote_book:"))
async def on_newnote_book_click(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    ref_raw = callback.data.split(":", maxsplit=1)[1]
    if not ref_raw.isdigit():
        await callback.answer("Некорректная кнопка", show_alert=True)
        return

    user_id = callback.from_user.id
    book = get_book_by_ref(user_id, int(ref_raw))
    if not book:
        await callback.answer("Книга не найдена", show_alert=True)
        return

    await state.clear()
    await state.update_data(book=book)
    await state.set_state(NoteFSM.waiting_note)
    await callback.answer()
    await callback.message.answer(f"Книга выбрана: {book}\n\nОтправь текст заметки.")


@router.callback_query(F.data == "browse_books")
async def on_browse_books(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    user_id = callback.from_user.id
    books = get_books(user_id)
    if not books:
        await safe_edit_message(callback.message, "Пока нет заметок по книгам.")
        await callback.answer()
        return

    await safe_edit_message(
        callback.message,
        "Выбери книгу:",
        reply_markup=books_keyboard(books),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("book:"))
async def on_book_click(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    user_id = callback.from_user.id
    ref_raw = callback.data.split(":", maxsplit=1)[1]
    if not ref_raw.isdigit():
        await callback.answer("Некорректная кнопка", show_alert=True)
        return

    book_ref_id = int(ref_raw)
    book = get_book_by_ref(user_id, book_ref_id)
    if not book:
        await callback.answer("Книга не найдена", show_alert=True)
        return

    categories = get_categories_by_book(user_id, book)
    if not categories:
        await callback.answer("В книге пока нет категорий", show_alert=True)
        return

    await safe_edit_message(
        callback.message,
        f"Книга: {book}\nВыбери категорию:",
        reply_markup=categories_keyboard(categories, book_ref_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cat:"))
async def on_category_click(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    user_id = callback.from_user.id
    ref_raw = callback.data.split(":", maxsplit=1)[1]
    if not ref_raw.isdigit():
        await callback.answer("Некорректная кнопка", show_alert=True)
        return

    category_ref_id = int(ref_raw)
    meta = get_note_meta_by_ref(user_id, category_ref_id)
    if not meta:
        await callback.answer("Категория не найдена", show_alert=True)
        return

    book, category = meta
    rows = get_notes_by_book_category(user_id, book, category)
    if not rows:
        await callback.answer("Заметки не найдены", show_alert=True)
        return

    book_ref_id = get_book_ref_id(user_id, book)
    if book_ref_id is None:
        await callback.answer("Книга не найдена", show_alert=True)
        return

    await safe_edit_message(
        callback.message,
        f"Книга: {book}\nКатегория: {category}\n\n{format_notes_list(rows)}",
        reply_markup=notes_keyboard(book_ref_id),
    )
    await callback.answer()


async def main() -> None:
    init_db()
    bot = Bot(token=get_bot_token())
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
