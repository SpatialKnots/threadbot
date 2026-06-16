from app.bot.keyboards import main_reply_keyboard, result_keyboard


def test_main_reply_keyboard_contains_start_button():
    keyboard = main_reply_keyboard()

    assert keyboard.keyboard[0][0].text == "START"
    assert keyboard.resize_keyboard is True


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

    assert [button.text for button in buttons] == ["Random", "Open VK", "Open 2ch"]
    assert buttons[-1].url == "https://2ch.hk/b/res/123.html#456"

    ocr_buttons = keyboard.inline_keyboard[-1]
    assert [button.text for button in ocr_buttons] == ["Image-to-text"]
    assert ocr_buttons[0].callback_data == "ocr:10"
