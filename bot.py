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
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def add_flow_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить", callback_data="newnote_add")],
            [InlineKeyboardButton(text="📚 Мои книги", callback_data="browse_books")],
        ]
    )


def open_notes_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📚 Мои книги", callback_data="browse_books")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_start")],
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
    buttons.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="back_start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def note_button_label(text: str, created_at: str) -> str:
    one_line = " ".join(text.split())
    return compact_label(f"{created_at} | {one_line}", max_len=56)


def category_notes_keyboard(
    notes: list[tuple[int, str, str]], book_ref_id: int, category_ref_id: int
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=note_button_label(text, created_at), callback_data=f"note:{note_id}"
            )
        ]
        for note_id, text, created_at in notes
    ]
    buttons.append(
        [InlineKeyboardButton(text="➕ Добавить", callback_data=f"catadd:{category_ref_id}")]
    )
    buttons.append(
        [InlineKeyboardButton(text="⬅️ К категориям", callback_data=f"book:{book_ref_id}")]
    )
    buttons.append([InlineKeyboardButton(text="📚 К книгам", callback_data="browse_books")])
    buttons.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="back_start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def note_view_keyboard(book_ref_id: int, category_ref_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К заметкам", callback_data=f"cat:{category_ref_id}")],
            [InlineKeyboardButton(text="⬅️ К категориям", callback_data=f"book:{book_ref_id}")],
            [InlineKeyboardButton(text="📚 К книгам", callback_data="browse_books")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="back_start")],
        ]
    )


def wait_book_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_start")],
        ]
    )


def wait_note_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_book_input")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="back_start")],
        ]
    )


def wait_category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_note_input")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="back_start")],
        ]
    )


def wait_note_in_category_keyboard(category_ref_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"cat:{category_ref_id}")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="back_start")],
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


def get_category_ref_id(user_id: int, book: str, category: str) -> Optional[int]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT MIN(id)
            FROM notes
            WHERE user_id = ? AND book = ? AND category = ?
            """,
            (user_id, book, category),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def get_category_notes(
    user_id: int, book: str, category: str
) -> list[tuple[int, str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT id, text, created_at
            FROM notes
            WHERE user_id = ? AND book = ? AND category = ?
            ORDER BY id DESC
            """,
            (user_id, book, category),
        ).fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


def get_note_by_id(user_id: int, note_id: int) -> Optional[tuple[str, str, str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT text, book, category, created_at
            FROM notes
            WHERE user_id = ? AND id = ?
            """,
            (user_id, note_id),
        ).fetchone()
    return (row[0], row[1], row[2], row[3]) if row else None


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
    await message.answer(
        "Выбери действие:",
        reply_markup=add_flow_keyboard(),
    )


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(NoteFSM.waiting_book)
    await message.answer("Напиши название книги.", reply_markup=wait_book_keyboard())


@router.message(Command("library"))
async def cmd_library(message: Message) -> None:
    user_id = message.from_user.id
    books = get_books(user_id)
    if not books:
        await message.answer("Пока нет заметок по книгам.", reply_markup=add_flow_keyboard())
        return
    await message.answer("Выбери книгу:", reply_markup=books_keyboard(books))


@router.message(Command("notes"))
async def cmd_notes(message: Message) -> None:
    await message.answer("Открой список книг:", reply_markup=open_notes_keyboard())


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
    await message.answer("Отправь текст заметки.", reply_markup=wait_note_keyboard())


@router.message(StateFilter(NoteFSM.waiting_note), F.text)
async def on_waiting_note(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = message.text.strip()
    fixed_book = data.get("book")
    fixed_category = data.get("fixed_category")
    category_ref_id = data.get("category_ref_id")

    if fixed_book and fixed_category:
        user_id = message.from_user.id
        save_note(user_id=user_id, text=text, book=fixed_book, category=fixed_category)
        await state.clear()

        notes = get_category_notes(user_id, fixed_book, fixed_category)
        book_ref_id = get_book_ref_id(user_id, fixed_book)
        resolved_category_ref = (
            get_category_ref_id(user_id, fixed_book, fixed_category) or category_ref_id
        )

        if not notes or book_ref_id is None or resolved_category_ref is None:
            await message.answer("Заметка добавлена!", reply_markup=open_notes_keyboard())
            return

        await message.answer(
            f"Заметка добавлена.\nКнига: {fixed_book}\nКатегория: {fixed_category}\n\n"
            "Выбери заметку:",
            reply_markup=category_notes_keyboard(
                notes, book_ref_id=book_ref_id, category_ref_id=resolved_category_ref
            ),
        )
        return

    await state.update_data(text=text)
    await state.set_state(NoteFSM.waiting_category)
    await message.answer(
        "К какой категории/теме относится эта мысль?",
        reply_markup=wait_category_keyboard(),
    )


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
    await callback.message.answer("Напиши название книги.", reply_markup=wait_book_keyboard())


@router.callback_query(F.data == "back_start")
async def on_back_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    await state.clear()
    await safe_edit_message(
        callback.message,
        "Выбери действие:",
        reply_markup=add_flow_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_book_input")
async def on_back_to_book_input(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    await state.set_data({})
    await state.set_state(NoteFSM.waiting_book)
    await safe_edit_message(
        callback.message,
        "Напиши название книги.",
        reply_markup=wait_book_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_note_input")
async def on_back_to_note_input(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    data = await state.get_data()
    book = data.get("book")
    if not book:
        await state.set_data({})
        await state.set_state(NoteFSM.waiting_book)
        await safe_edit_message(
            callback.message,
            "Напиши название книги.",
            reply_markup=wait_book_keyboard(),
        )
        await callback.answer()
        return

    await state.set_data({"book": book})
    await state.set_state(NoteFSM.waiting_note)
    await safe_edit_message(
        callback.message,
        "Отправь текст заметки.",
        reply_markup=wait_note_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "browse_books")
async def on_browse_books(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    user_id = callback.from_user.id
    books = get_books(user_id)
    if not books:
        await safe_edit_message(
            callback.message,
            "Пока нет заметок по книгам.\n\nВыбери действие:",
            reply_markup=add_flow_keyboard(),
        )
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
    notes = get_category_notes(user_id, book, category)
    if not notes:
        await callback.answer("Заметки не найдены", show_alert=True)
        return

    book_ref_id = get_book_ref_id(user_id, book)
    if book_ref_id is None:
        await callback.answer("Книга не найдена", show_alert=True)
        return

    await safe_edit_message(
        callback.message,
        f"Книга: {book}\nКатегория: {category}\n\nВыбери заметку:",
        reply_markup=category_notes_keyboard(
            notes,
            book_ref_id=book_ref_id,
            category_ref_id=category_ref_id,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("catadd:"))
async def on_category_add_note(callback: CallbackQuery, state: FSMContext) -> None:
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
    await state.clear()
    await state.set_data(
        {
            "book": book,
            "fixed_category": category,
            "category_ref_id": category_ref_id,
        }
    )
    await state.set_state(NoteFSM.waiting_note)
    await safe_edit_message(
        callback.message,
        f"Книга: {book}\nКатегория: {category}\n\nОтправь текст новой заметки.",
        reply_markup=wait_note_in_category_keyboard(category_ref_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("note:"))
async def on_note_click(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    user_id = callback.from_user.id
    ref_raw = callback.data.split(":", maxsplit=1)[1]
    if not ref_raw.isdigit():
        await callback.answer("Некорректная кнопка", show_alert=True)
        return

    note_id = int(ref_raw)
    note = get_note_by_id(user_id, note_id)
    if not note:
        await callback.answer("Заметка не найдена", show_alert=True)
        return

    _, book, category, _ = note
    book_ref_id = get_book_ref_id(user_id, book)
    category_ref_id = get_category_ref_id(user_id, book, category)
    if book_ref_id is None or category_ref_id is None:
        await callback.answer("Навигация недоступна", show_alert=True)
        return

    await safe_edit_message(
        callback.message,
        format_note(note),
        reply_markup=note_view_keyboard(book_ref_id, category_ref_id),
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
