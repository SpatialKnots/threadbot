from app.bot.keyboards import (
    HELP_BUTTON,
    LATEST_BUTTON,
    RANDOM_BUTTON,
    SEARCH_BUTTON,
    main_reply_keyboard,
    result_keyboard,
    welcome_inline_keyboard,
)


def test_main_reply_keyboard_contains_action_buttons():
    keyboard = main_reply_keyboard()

    assert [button.text for row in keyboard.keyboard for button in row] == [
        SEARCH_BUTTON,
        RANDOM_BUTTON,
        LATEST_BUTTON,
        HELP_BUTTON,
    ]
    assert keyboard.resize_keyboard is True


def test_welcome_inline_keyboard_contains_action_callbacks():
    keyboard = welcome_inline_keyboard()

    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert [button.text for button in buttons] == [SEARCH_BUTTON, RANDOM_BUTTON, LATEST_BUTTON, HELP_BUTTON]
    assert [button.callback_data for button in buttons] == ["search_help", "random", "latest", "help"]


def test_result_keyboard_includes_2ch_link_when_available():
    keyboard = result_keyboard(
        query_id=1,
        index=0,
        total=1,
        vk_url="https://vk.com/wall-1_1",
        post_id=10,
        original_url="https://2ch.hk/b/res/123.html#456",
    )

    buttons = keyboard.inline_keyboard[-2]

    assert [button.text for button in buttons] == [RANDOM_BUTTON, "Open VK", "Open 2ch"]
    assert buttons[-1].url == "https://2ch.hk/b/res/123.html#456"

    ocr_buttons = keyboard.inline_keyboard[-1]
    assert [button.text for button in ocr_buttons] == ["Image-to-text"]
    assert ocr_buttons[0].callback_data == "ocr:10"
