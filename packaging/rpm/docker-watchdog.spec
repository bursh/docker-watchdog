Name:           docker-watchdog
Version:        1.3.0
Release:        1%{?dist}
Summary:        Label-driven HTTP and healthcheck recovery for Docker Compose
License:        Apache-2.0
URL:            https://github.com/bursh/docker-watchdog
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
Requires:       python3 >= 3.7
Requires:       ca-certificates
Requires:       systemd
Requires(post): systemd
Requires(preun): systemd
Requires(postun): systemd

%description
A centralized watchdog for opt-in Docker Compose services. It discovers
published HTTP ports or Docker health status, tracks failures independently,
and coordinates container and whole-project recovery. Docker Engine and
Docker Compose V2 must already be installed and configured on the host.

%prep
%setup -q

%build
# Standard-library Python application; no compilation required.

%install
install -D -m 0755 watchdog.py %{buildroot}%{_bindir}/docker-watchdog
install -D -m 0644 watchdog.conf %{buildroot}%{_sysconfdir}/docker-watchdog/watchdog.conf
install -D -m 0644 docker-watchdog.service %{buildroot}/usr/lib/systemd/system/docker-watchdog.service
install -D -m 0644 watchdog.conf %{buildroot}%{_datadir}/doc/docker-watchdog/watchdog.conf.example
install -D -m 0644 README.md %{buildroot}%{_datadir}/doc/docker-watchdog/README.md
install -D -m 0644 CHANGELOG.md %{buildroot}%{_datadir}/doc/docker-watchdog/CHANGELOG.md
install -D -m 0644 LICENSE %{buildroot}%{_datadir}/licenses/docker-watchdog/LICENSE
install -d -m 0755 %{buildroot}%{_mandir}/man1
# gzip without an embedded file timestamp; works with standard RPM tooling.
gzip -n -9 -c docs/docker-watchdog.1 > %{buildroot}%{_mandir}/man1/docker-watchdog.1.gz
chmod 0644 %{buildroot}%{_mandir}/man1/docker-watchdog.1.gz

%post
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || :
    if [ "$1" -eq 1 ]; then
        systemctl enable docker-watchdog.service >/dev/null 2>&1 || :
        if command -v docker >/dev/null && docker compose version >/dev/null 2>&1 && systemctl is-active --quiet docker.service; then
            systemctl start docker-watchdog.service >/dev/null 2>&1 || :
        else
            echo 'Docker Engine or Compose V2 unavailable; start docker-watchdog after Docker is ready.' >&2
        fi
    else
        # Preserve an administrator's disabled/stopped service during upgrades.
        systemctl try-restart docker-watchdog.service >/dev/null 2>&1 || :
    fi
fi

%preun
if [ "$1" -eq 0 ] && [ -d /run/systemd/system ]; then
    systemctl disable --now docker-watchdog.service >/dev/null 2>&1 || :
fi

%postun
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || :
fi

%files
%dir %{_sysconfdir}/docker-watchdog
%config(noreplace) %{_sysconfdir}/docker-watchdog/watchdog.conf
%{_bindir}/docker-watchdog
/usr/lib/systemd/system/docker-watchdog.service
%dir %{_datadir}/doc/docker-watchdog
%doc %{_datadir}/doc/docker-watchdog/README.md
%doc %{_datadir}/doc/docker-watchdog/CHANGELOG.md
%doc %{_datadir}/doc/docker-watchdog/watchdog.conf.example
%dir %{_datadir}/licenses/docker-watchdog
%license %{_datadir}/licenses/docker-watchdog/LICENSE
%{_mandir}/man1/docker-watchdog.1*
