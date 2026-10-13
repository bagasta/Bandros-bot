from apps.api.app.domain import RiskClass
from apps.api.app.firecracker_computer import VNC_PORT, VIEWER_PORT, FirecrackerComputer
from apps.api.app.policy import PolicyEngine
from apps.api.app.runtime import user_visible_text


def test_approval_reasons_are_indonesian():
    engine = PolicyEngine()
    decision = engine.decide(RiskClass.EXTERNAL_WRITE)
    assert decision.requires_approval
    assert decision.reason
    assert "Setujui sekali" in decision.reason
    assert "require explicit approval" not in decision.reason


def test_raw_provider_errors_stay_out_of_chat():
    assert "log server" in user_visible_text("status_code 404 from openai/gpt-4.1")
    assert user_visible_text("Sudah kubuat catatannya.") == "Sudah kubuat catatannya."


def test_bandros_desktop_does_not_share_the_other_vm_ports(tmp_path):
    computer = FirecrackerComputer(account_id="bandros-account", root=tmp_path)
    assert VNC_PORT == 5912
    assert VIEWER_PORT == 6090
    assert computer.vnc_port == 5912
    assert computer.viewer_port == 6090
    assert "6090" in computer.screen_url
    assert "6080" not in computer.screen_url
