from src.core.output_dispatcher import OutputDispatcher, OutputMessage


class _Sender:
    def __init__(self):
        self.sent: list[str] = []

    def send_chatbox(self, text: str) -> bool:
        self.sent.append(text)
        return True


def test_output_dispatcher_formats_second_target_output():
    dispatcher = OutputDispatcher(
        {
            "translation": {
                "output_format": "translated1_with_translated2_original",
            }
        }
    )

    text = dispatcher.format_chatbox_output("原文", "译文1", "译文2")

    assert text == "译文1(译文2)(原文)"


def test_output_dispatcher_template_skips_missing_optional_lines():
    dispatcher = OutputDispatcher(
        {
            "translation": {
                "output_format": "translated_only",
                "chatbox_template": "{translatedText}\n{translatedText2}\n{text}",
            }
        }
    )

    text = dispatcher.format_chatbox_output("原文", "译文1", "")

    assert text == "译文1\n原文"


def test_output_dispatcher_second_target_formats_degrade_without_second_text():
    assert OutputDispatcher(
        {"translation": {"output_format": "original_with_translated1_translated2"}}
    ).format_chatbox_output("原文", "译文1", "") == "原文(译文1)"
    assert OutputDispatcher(
        {"translation": {"output_format": "translated1_with_translated2"}}
    ).format_chatbox_output("原文", "译文1", "") == "译文1"
    assert OutputDispatcher(
        {"translation": {"output_format": "translated1_with_translated2_original"}}
    ).format_chatbox_output("原文", "译文1", "") == "译文1(原文)"


def test_output_dispatcher_original_only_ignores_chatbox_template():
    dispatcher = OutputDispatcher(
        {
            "translation": {
                "output_format": "original_only",
                "chatbox_template": "{translatedText}\n{text}",
            }
        }
    )

    text = dispatcher.format_chatbox_output("原文", "译文1")

    assert text == "原文"


def test_output_dispatcher_sends_formatted_chatbox_payload():
    dispatcher = OutputDispatcher(
        {
            "translation": {
                "output_format": "original_with_translated1_translated2",
            }
        }
    )
    sender = _Sender()

    sent = dispatcher.send_chatbox(
        sender,
        original_text="原文",
        translated_text="訳文",
        translated_text_2="translation",
    )

    assert sent is True
    assert sender.sent == ["原文(訳文)(translation)"]


def test_output_dispatcher_dispatches_to_registered_sinks():
    seen: list[OutputMessage] = []
    dispatcher = OutputDispatcher({}, {"ui": lambda message: seen.append(message) or True})
    message = dispatcher.build_message(
        source="manual",
        original_text="hello",
        translated_text="こんにちは",
    )

    result = dispatcher.dispatch(message)

    assert result == {"ui": True}
    assert seen == [message]
    assert message.chatbox_text == "こんにちは(hello)"


def test_output_dispatcher_isolates_sink_failures():
    seen: list[str] = []

    def bad_sink(_message):
        raise RuntimeError("boom")

    dispatcher = OutputDispatcher(
        {},
        {
            "bad": bad_sink,
            "good": lambda message: seen.append(message.display_text) or True,
        },
    )
    message = OutputMessage(source="listen", display_text="ok")

    result = dispatcher.dispatch(message)

    assert result == {"bad": False, "good": True}
    assert seen == ["ok"]
