from src.core.runtime_controller import RuntimeController


class TestRuntimeController:
    def test_initial_state_idle(self):
        ctrl = RuntimeController()
        assert not ctrl.is_running()
        assert not ctrl.is_muted()

    def test_start_transitions_to_running(self):
        ctrl = RuntimeController()
        ctrl.start()
        assert ctrl.is_running()

    def test_stop_transitions_to_idle(self):
        ctrl = RuntimeController()
        ctrl.start()
        ctrl.stop()
        assert not ctrl.is_running()

    def test_toggle_mute_from_unmuted(self):
        ctrl = RuntimeController()
        result = ctrl.toggle_mute()
        assert result is True
        assert ctrl.is_muted()

    def test_toggle_mute_from_muted(self):
        ctrl = RuntimeController()
        ctrl._muted = True
        result = ctrl.toggle_mute()
        assert result is False
        assert not ctrl.is_muted()

    def test_start_idempotent(self):
        ctrl = RuntimeController()
        ctrl.start()
        ctrl.start()
        assert ctrl.is_running()

    def test_stop_idempotent(self):
        ctrl = RuntimeController()
        ctrl.stop()
        assert not ctrl.is_running()
