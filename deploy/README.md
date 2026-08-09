# 상시 실행 서버 배포

`deploy/systemd` 파일은 `/opt/stockAlarm`에 설치한 Linux 서버용입니다. `.env`와 `data/`는 서버의 영구 디스크에 보관해야 SQLite 이력이 유지됩니다.

1. 저장소를 `/opt/stockAlarm`에 배치하고 가상환경과 의존성을 설치합니다.
2. `.env`에 Telegram 및 선택 API 키를 설정합니다.
3. 서비스·타이머 파일을 `/etc/systemd/system/`에 복사합니다.
4. `systemctl daemon-reload` 후 세 타이머를 enable/start 합니다.

실제 서버 주소와 접근 권한이 없으므로 이 저장소에서는 배포 설정까지만 제공합니다. Windows PC에서는 작업 스케줄러가 동일한 역할을 수행합니다.
