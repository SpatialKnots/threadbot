from app.bot.keyboards import result_keyboard


def test_result_keyboard_includes_2ch_link_when_available():
    keyboard = result_keyboard(
        query_id=1,
        index=0,
        total=1,
        vk_url="https://vk.com/wall-1_1",
        original_url="https://2ch.hk/b/res/123.html#456",
    )

    buttons = keyboard.inline_keyboard[-1]

    assert [button.text for button in buttons] == ["Random", "Open VK", "Open 2ch"]
    assert buttons[-1].url == "https://2ch.hk/b/res/123.html#456"
