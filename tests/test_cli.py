import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from runner import cli


def test_serve_defaults_to_detected_tailscale_address(monkeypatch,capsys):
 import uvicorn
 import runner.web
 probe=Mock(return_value=SimpleNamespace(stdout='100.101.102.103\n'))
 application=object();factory=Mock(return_value=application);run=Mock()
 monkeypatch.setattr(cli.subprocess,'run',probe)
 monkeypatch.setattr(runner.web,'app',factory)
 monkeypatch.setattr(uvicorn,'run',run)
 monkeypatch.setattr(cli.sys,'argv',['research-runner','--state','./var','serve'])
 cli.main()
 probe.assert_called_once_with(['tailscale','ip','-4'],capture_output=True,text=True,check=True,timeout=5)
 factory.assert_called_once_with('./var','docker','research-runner-worker:0.2.0')
 run.assert_called_once_with(application,host='100.101.102.103',port=8765)
 assert 'http://100.101.102.103:8765' in capsys.readouterr().out


def test_explicit_host_skips_tailscale(monkeypatch):
 import uvicorn
 import runner.web
 probe=Mock(side_effect=AssertionError('must not probe Tailscale'))
 run=Mock();application=object()
 monkeypatch.setattr(cli.subprocess,'run',probe)
 monkeypatch.setattr(runner.web,'app',Mock(return_value=application))
 monkeypatch.setattr(uvicorn,'run',run)
 monkeypatch.setattr(cli.sys,'argv',['research-runner','serve','--host','127.0.0.1','--port','9876'])
 cli.main()
 probe.assert_not_called()
 run.assert_called_once_with(application,host='127.0.0.1',port=9876)


@pytest.mark.parametrize('result',[
 FileNotFoundError('tailscale'),
 subprocess.TimeoutExpired(['tailscale','ip','-4'],5),
 subprocess.CalledProcessError(1,['tailscale','ip','-4']),
 '', 'not-an-address', '100.101.102.103\n100.101.102.104\n', '0.0.0.0',
])
def test_discovery_failure_does_not_create_service_or_fall_back(monkeypatch,capsys,result):
 import runner.web
 probe=Mock(side_effect=result) if isinstance(result,Exception) else Mock(return_value=SimpleNamespace(stdout=result))
 factory=Mock(side_effect=AssertionError('must not create service'))
 monkeypatch.setattr(cli.subprocess,'run',probe)
 monkeypatch.setattr(runner.web,'app',factory)
 monkeypatch.setattr(cli.sys,'argv',['research-runner','serve'])
 with pytest.raises(SystemExit) as error:cli.main()
 assert error.value.code==2
 factory.assert_not_called()
 assert 'Cannot discover Tailscale IPv4 address' in capsys.readouterr().err
