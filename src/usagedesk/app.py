from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from .storage import ConfigStore
from .ui import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="UsageDesk 사용량 바")
    parser.add_argument("--data-dir", type=Path, help="개발·시험용 설정 위치")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--window", action="store_true", help="사용량 바와 상세 창을 함께 표시")
    parser.add_argument("--smoke-ms", type=int, default=0, help="시험용 자동 종료 시간")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("UsageDesk")
    if sys.platform != "win32":
        QMessageBox.critical(None, "지원 환경", "UsageDesk는 Windows 전용입니다.")
        return 1
    directory = (args.data_dir or Path(os.environ["LOCALAPPDATA"]) / "UsageDesk").resolve()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        QMessageBox.critical(None, "시작 실패", "설정 폴더에 접근할 수 없습니다.")
        return 1
    server_name = "UsageDesk-" + hashlib.sha256(str(directory).casefold().encode()).hexdigest()[:24]
    lock = QLockFile(str(directory / "instance.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        socket = QLocalSocket()
        # Bounded startup retries: the first process may not have bound its pipe yet.
        for _ in range(5):
            socket.connectToServer(server_name)
            if socket.waitForConnected(200):
                socket.write(b"show\n")
                socket.waitForBytesWritten(1000)
                socket.disconnectFromServer()
                return 0
            socket.abort()
            time.sleep(0.1)  # Bounded startup grace period before any window is created.
        QMessageBox.warning(
            None,
            "이미 실행 중",
            "기존 인스턴스에 연결할 수 없습니다. 기존 앱의 종료 여부를 확인하세요.",
        )
        return 1
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    QLocalServer.removeServer(server_name)  # Only after exclusive lock acquisition.
    if not server.listen(server_name):
        lock.unlock()
        QMessageBox.critical(None, "시작 실패", "단일 인스턴스 통신을 시작할 수 없습니다.")
        return 1
    window = MainWindow(ConfigStore(directory), tray_enabled=not bool(args.smoke_ms))
    clients = set()

    def accept_client():
        while server.hasPendingConnections():
            client = server.nextPendingConnection()
            clients.add(client)
            buffer = bytearray()

            def read(c=client, data=buffer):
                data.extend(bytes(c.read(16)))
                if bytes(data) == b"show\n":
                    window.open_tab(window.tabs.currentIndex())
                    c.disconnectFromServer()
                elif len(data) >= 5 or c.bytesAvailable() > 0:
                    c.disconnectFromServer()

            def cleanup(c=client):
                clients.discard(c)
                c.deleteLater()

            client.readyRead.connect(read)
            client.disconnected.connect(cleanup)
            QTimer.singleShot(2000, client, client.disconnectFromServer)
            if client.bytesAvailable():
                read()

    server.newConnection.connect(accept_client)
    app.setQuitOnLastWindowClosed(False)
    if not args.background or not window.tray_available:
        window.bar.show_bar()
    if args.window:
        window.show()
    if args.smoke_ms:
        QTimer.singleShot(args.smoke_ms, window.quit)
    result = app.exec()
    window.timer.stop()
    window.pool.clear()
    window.pool.waitForDone()  # Only waits for local requests, never for launched CMD lifetime.
    window.usage.shutdown()
    window.usage.pool.waitForDone()
    server.close()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
