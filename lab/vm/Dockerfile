# Small Debian "server" for the NightWatch lab: systemd as PID 1, journald, sshd,
# and one application service (shop-worker.service).
FROM debian:bookworm-20250203-slim

ENV container=docker DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      systemd systemd-sysv dbus openssh-server procps iproute2 less \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN systemctl mask \
      systemd-udevd.service systemd-udevd-kernel.socket systemd-udevd-control.socket \
      systemd-modules-load.service systemd-remount-fs.service systemd-firstboot.service \
      sys-kernel-config.mount sys-kernel-debug.mount sys-kernel-tracing.mount \
      dev-hugepages.mount getty.target console-getty.service \
 && rm -f /etc/ssh/ssh_host_* \
 && useradd --system --home-dir /var/lib/shop-worker --no-create-home --shell /usr/sbin/nologin shop \
 && useradd --create-home --shell /bin/bash --groups systemd-journal,adm nightwatch \
 && usermod --password '*' nightwatch \
 && install -d -m 0700 /opt/provision \
 && install -d -m 2755 -g systemd-journal /var/log/journal \
 && echo 'LANG=C.UTF-8' > /etc/default/locale \
 && cat /proc/sys/kernel/random/uuid | tr -d '-' > /etc/machine-id

COPY rootfs/ /

RUN chmod 0755 /usr/local/bin/shop-worker /usr/local/sbin/install-ssh-keys \
 && systemctl enable ssh-keys.service ssh.service shop-worker.service

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
