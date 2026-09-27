"""HTTP 클라이언트: 재시도, 사이트별 요청 간격, 불완전한 인증서 체인 보완.

일부 공공기관 서버는 중간 인증서(intermediate CA)를 보내지 않아 브라우저에서는 열리지만
파이썬에서는 인증서 검증에 실패한다. 이 경우 서버 인증서의 AIA(Authority Information
Access) 항목에서 중간 인증서를 내려받아 신뢰 목록(certifi)에 덧붙인 뒤 다시 검증한다.
루트 인증서까지의 검증은 그대로 유지되므로 검증을 끄는 것과 달리 안전하다.

또 일부 새올(eminwon) 서버는 오래된 TLS 설정(짧은 DH 키, 구형 암호군)만 지원해서 최신 OpenSSL
기본값으로는 연결 자체가 안 된다. sources.yaml 에서 legacy_tls: true 로 지정한 호스트에 한해
암호 강도 하한만 낮춘 연결을 쓴다 (인증서 검증은 그대로).

접속 자체가 안 되는 서버(해외 접속 차단·장애)는 기다리는 시간이 전체 실행 시간을 잡아먹는다.
연결 대기는 10초씩 두 번까지만 하고, 한 번 접속에 실패한 서버는 같은 실행 안에서 다시 기다리지
않고 바로 실패시킨다 (같은 서버의 다른 게시판들). 실행 끝의 재시도 전에 forget_unreachable() 로 잊는다.
"""
from __future__ import annotations

import logging
import socket
import ssl
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import ConnectTimeoutError
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36 UlsanInstructorJobs/1.0 "
    "(+https://github.com/thskal17-sudo/ulsan-shcool-public-company)"
)


class HostUnreachable(requests.exceptions.ConnectionError):
    """이번 실행에서 이미 접속하지 못한 서버라 기다리지 않고 건너뜀."""


class Http:
    def __init__(
        self,
        min_interval: float = 1.0,
        timeout: float = 20.0,
        retries: int = 2,
        max_seconds: float = 60.0,
        max_bytes: int = 10_000_000,
        connect_timeout: float = 10.0,
        connect_retries: int = 1,
    ):
        self.min_interval = min_interval
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.max_seconds = max_seconds  # 응답 하나를 받는 데 쓰는 최대 시간 (느리게 흘려보내는 서버 대비)
        self.max_bytes = max_bytes
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"})
        retry = Retry(
            total=retries,
            connect=min(connect_retries, retries),
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
        )
        self._retry = retry
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self._last_request: dict[str, float] = {}
        self._ca_bundles: dict[str, str] = {}
        self._unreachable: set[str] = set()
        self._lock = threading.Lock()

    def allow_legacy_tls(self, host: str) -> None:
        """이 호스트에만 구형 TLS 설정(짧은 DH 키·구형 암호군·TLS 1.0/1.1)을 허용한다."""
        self.session.mount(f"https://{host}/", LegacyTLSAdapter(max_retries=self._retry))

    def forget_unreachable(self) -> None:
        """접속 실패로 기억해 둔 서버를 잊는다 (실행 끝에서 한 번 더 시도하기 전에)."""
        self._unreachable.clear()

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        host = urlsplit(url).hostname or ""
        if host in self._unreachable:
            raise HostUnreachable(f"앞서 접속하지 못한 서버라 건너뜀: {host}")
        self._wait_turn(host)
        kwargs.setdefault("timeout", (self.connect_timeout, self.timeout))
        kwargs["stream"] = True
        if host in self._ca_bundles:
            kwargs.setdefault("verify", self._ca_bundles[host])
        try:
            resp = self.session.request(method, url, **kwargs)
        except requests.exceptions.SSLError as exc:
            if host in self._ca_bundles or "verify" in kwargs or not _is_chain_error(exc):
                raise
            bundle = self._bundle_with_intermediates(host, urlsplit(url).port or 443)
            if not bundle:
                raise
            kwargs["verify"] = bundle
            resp = self.session.request(method, url, **kwargs)
        except requests.exceptions.ConnectionError as exc:
            if is_connect_failure(exc):
                self._unreachable.add(host)
            raise
        self._read_body(resp)
        resp.raise_for_status()
        return resp

    def _read_body(self, resp: requests.Response) -> None:
        """본문을 시간·크기 제한 안에서 읽는다 (requests 의 timeout 은 바이트 사이 간격만 본다)."""
        started = time.monotonic()
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in resp.iter_content(chunk_size=65536):
                chunks.append(chunk)
                size += len(chunk)
                if size > self.max_bytes:
                    raise requests.exceptions.ContentDecodingError(f"응답이 너무 큼 (>{self.max_bytes}B): {resp.url}")
                if time.monotonic() - started > self.max_seconds:
                    raise requests.exceptions.Timeout(f"응답 수신이 {self.max_seconds:.0f}초를 넘음: {resp.url}")
        finally:
            resp.close()
        resp._content = b"".join(chunks)  # noqa: SLF001 - stream 으로 읽은 본문을 일반 응답처럼 쓰기 위함

    def _wait_turn(self, host: str) -> None:
        with self._lock:
            last = self._last_request.get(host)
            now = time.monotonic()
            if last is not None and now - last < self.min_interval:
                time.sleep(self.min_interval - (now - last))
            self._last_request[host] = time.monotonic()

    def _bundle_with_intermediates(self, host: str, port: int) -> str | None:
        try:
            pems = fetch_intermediate_pems(host, port, self.session)
        except Exception as exc:  # noqa: BLE001 - 보완 실패 시 원래 SSL 오류를 그대로 올린다
            log.warning("중간 인증서 보완 실패 %s: %s", host, exc)
            return None
        if not pems:
            return None
        base = Path(certifi.where()).read_text(encoding="ascii")
        tmp = tempfile.NamedTemporaryFile("w", suffix=".pem", prefix=f"ca-{host}-", delete=False, encoding="ascii")
        with tmp:
            tmp.write(base)
            tmp.write("\n")
            tmp.write("\n".join(pems))
        log.info("중간 인증서 %d개 보완: %s", len(pems), host)
        self._ca_bundles[host] = tmp.name
        return tmp.name


class LegacyTLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context(cafile=certifi.where())
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        ctx.minimum_version = ssl.TLSVersion.TLSv1
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def is_connect_failure(exc: requests.exceptions.ConnectionError) -> bool:
    """연결 단계에서 실패했는가 (시간 초과·연결 거부·주소 찾기 실패). 응답 도중 끊긴 경우는 제외."""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    reason = getattr(exc.args[0], "reason", None) if exc.args else None
    return isinstance(reason, ConnectTimeoutError)  # urllib3 의 NewConnectionError 도 이 하위 클래스


def _is_chain_error(exc: Exception) -> bool:
    text = str(exc)
    return "CERTIFICATE_VERIFY_FAILED" in text and (
        "unable to get local issuer certificate" in text
        or "unable to verify the first certificate" in text
    )


def fetch_intermediate_pems(host: str, port: int, session: requests.Session) -> list[str]:
    """서버 인증서의 AIA 'CA Issuers' 주소를 따라가며 중간 인증서를 PEM 목록으로 받는다."""
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
    from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=15) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    cert = x509.load_der_x509_certificate(der)

    pems: list[str] = []
    for _ in range(4):
        try:
            aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
        except x509.ExtensionNotFound:
            break
        urls = [d.access_location.value for d in aia if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS]
        if not urls:
            break
        data = session.get(urls[0], timeout=(10, 15)).content
        issuer = _load_any_cert(data, x509, pkcs7)
        if issuer is None:
            break
        pems.append(issuer.public_bytes(Encoding.PEM).decode("ascii"))
        if issuer.issuer == issuer.subject:
            break
        cert = issuer
    return pems


def _load_any_cert(data: bytes, x509, pkcs7):
    for loader in (x509.load_der_x509_certificate, x509.load_pem_x509_certificate):
        try:
            return loader(data)
        except ValueError:
            pass
    for loader in (pkcs7.load_der_pkcs7_certificates, pkcs7.load_pem_pkcs7_certificates):
        try:
            certs = loader(data)
            if certs:
                return certs[0]
        except ValueError:
            pass
    return None
