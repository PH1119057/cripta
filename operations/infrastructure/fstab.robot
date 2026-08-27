LABEL=cloudimg-rootfs / ext4 errors=panic,barrier=1 0 1
/var/log/cripta /srv/cripta-share/logs none bind,ro,nosuid,nodev,noexec 0 0
UUID=338302c0-6a02-4d6c-8aa1-643aa96c90f8 /data/cripta ext4 defaults,nofail,noatime 0 2
UUID=d52a3bfd-d7f3-4ec7-9454-14e60955066b /mnt/cripta-fast ext4 noauto,nofail,noatime 0 2
