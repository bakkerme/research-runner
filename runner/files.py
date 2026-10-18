"""Bounded reads through directory descriptors; no symlink traversal."""
import os
import stat

from .package import MAX_FILE, relpath


def read_file(root, name, limit=MAX_FILE):
    relpath(name)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = name.split('/')
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(child, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('unsafe or oversized file: ' + name)
            data = source.read(limit + 1)
            after = os.fstat(source.fileno())
            if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('file changed during publication: ' + name)
            return data
    finally:
        os.close(fd)
