# Scripts de Deploy e Telemetria — TV Box (Debian ARM64)

Scripts para provisionamento, sincronização e monitoramento da TV Box (S905W / ARM64):

- `install_tvbox.sh`: Script de setup inicial em Debian ARM64 (instala dependências, python3-venv, cloudflared, systemd unit).
- `deploy_tvbox.py`: Deploy do código via SSH e SFTP.
- `sync_to_tvbox.py`: Sincroniza extratos e banco para a TV Box.
- `setup_timer.py`: Configura o systemd timer para rodar a cada 60min no pregão.
- `setup_tvbox_api.py`: Configura e inicia o serviço systemd da API web.
- `test_service.py`: Inspeciona o journalctl do serviço `barsi.service`.
- `test_ai_tvbox.py`: Testa conectividade e inferência IA remota a partir da TV Box.
