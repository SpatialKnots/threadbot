from app.bot.keyboards import (
    FAVORITE_ADD_BUTTON,
    FAVORITE_REMOVE_BUTTON,
    HELP_BUTTON,
    LATEST_BUTTON,
    RANDOM_BUTTON,
    SEARCH_BUTTON,
    SIMILAR_BUTTON,
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

    buttons = keyboard.inline_keyboard[-3]

    assert [button.text for button in buttons] == [RANDOM_BUTTON, "Open VK", "Open 2ch"]
    assert buttons[-1].url == "https://2ch.hk/b/res/123.html#456"

    favorite_buttons = keyboard.inline_keyboard[-2]
    assert [button.text for button in favorite_buttons] == [FAVORITE_ADD_BUTTON, SIMILAR_BUTTON]
    assert favorite_buttons[0].callback_data == "fav:add:10"
    assert favorite_buttons[1].callback_data == "similar:10"

    ocr_buttons = keyboard.inline_keyboard[-1]
    assert [button.text for button in ocr_buttons] == ["Image-to-text"]
    assert ocr_buttons[0].callback_data == "ocr:10"


def test_result_keyboard_can_show_remove_favorite_button():
    keyboard = result_keyboard(
        query_id=0,
        index=0,
        total=1,
        vk_url="https://vk.com/wall-1_1",
        post_id=10,
        favorite_action="remove",
    )

    favorite_button = keyboard.inline_keyboard[-2][0]

    assert favorite_button.text == FAVORITE_REMOVE_BUTTON
    assert favorite_button.callback_data == "fav:remove:10"
