"""Keep an SSH API tunnel in a Docker backend's network namespace.

Run under a host systemd service as root (nsenter requires this privilege).
The SSH identity should belong to a remote account restricted to forwarding the
sandbox API. No host port is published and no SSH client is added to the app.
"""
import argparse
import signal
import subprocess
import sys
import time


def container_identity(name):
    result = subprocess.run(
        ['docker', 'inspect', '--format', '{{.Id}} {{.State.Pid}} {{.State.Running}}', name],
        capture_output=True, text=True, timeout=10,
    )
    fields = result.stdout.strip().split()
    if result.returncode or len(fields) != 3 or fields[2] != 'true' or fields[1] == '0':
        return None
    return fields[0], int(fields[1])


def stop(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def run(args):
    while True:
        identity = container_identity(args.container)
        if identity is None:
            time.sleep(3)
            continue
        command = [
            'nsenter', '--target', str(identity[1]), '--net',
            'ssh', '-F', '/dev/null', '-N', '-T',
            '-i', args.identity, '-o', 'IdentitiesOnly=yes',
            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + args.known_hosts,
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            '-L', f'127.0.0.1:{args.local_port}:127.0.0.1:{args.remote_port}',
            args.destination,
        ]
        print(f'Connecting sandbox API for {args.container} ({identity[0][:12]})', flush=True)
        child = subprocess.Popen(command)
        try:
            while child.poll() is None:
                time.sleep(3)
                if container_identity(args.container) != identity:
                    print('Backend container changed; rebinding tunnel', flush=True)
                    break
        finally:
            stop(child)
        time.sleep(3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default='paperagent')
    parser.add_argument('--destination', required=True)
    parser.add_argument('--identity', required=True)
    parser.add_argument('--known-hosts', required=True)
    parser.add_argument('--local-port', type=int, default=18080)
    parser.add_argument('--remote-port', type=int, default=18080)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    run(args)


if __name__ == '__main__':
    main()
