"""Offline process adapter using the production worker on a separate server tree.

Only trusted test snippets run locally. This does not register an app provider.
"""
import asyncio
from pathlib import Path
import shlex
import shutil
import sys
from types import SimpleNamespace

from ai_system.sandbox import job_worker
from ai_system.sandbox.opensandbox_provider import OpenSandboxSession
from helpers.remote_files import RemoteFiles


class ProcessJobs(RemoteFiles):
    execution_mode = 'process'
    poll_interval = .02
    _cursor = OpenSandboxSession._cursor
    poll = OpenSandboxSession.poll
    execute = OpenSandboxSession.execute
    recover = OpenSandboxSession.recover
    interrupt = OpenSandboxSession.interrupt
    submit = OpenSandboxSession.submit
    _follow = OpenSandboxSession._follow

    def __init__(self, workspace, server, *, settings=None):
        super().__init__(workspace, server, settings=settings)
        self.root = str(self.remote)
        self.active_job = None
        self._job_cursors = {}
        self._job_outputs = {}
        self.operations = []
        self.lost_submit_reply = False
        self.read_failures = 0
        self.offline = False
        self.block_stop = False
        self.closes = 0
        self.helper = Path(server) / 'helpers' / 'paperagent_job.py'
        self.helper.parent.mkdir(parents=True, exist_ok=True)
        self.bootstrap = self.helper.with_name('code-interpreter-env.sh')
        self.bootstrap.write_text(
            'export PATH=' + shlex.quote(str(Path(sys.executable).parent)) + ':"$PATH"\n'
            'echo "Switched to Python Python 3.11.15"\n')
        self.rpc_outputs = []
        shutil.copyfile(job_worker.__file__, self.helper)
        shutil.copyfile(Path(job_worker.__file__).with_name('runtime_helpers.py'),
                        self.helper.with_name('paperagent_runtime.py'))

        async def mkdir(entries):
            for entry in entries:
                Path(entry.path).mkdir(parents=True, exist_ok=True)

        async def write(path, data, **kwargs):
            Path(path).write_text(data)

        self.sandbox = SimpleNamespace(files=SimpleNamespace(create_directories=mkdir, write_file=write))

    async def _sustain(self, needed):
        await asyncio.Event().wait()

    async def _job_rpc(self, op, run_id, argument=None):
        self.operations.append((op, run_id))
        if self.offline or (self.block_stop and op == 'stop'):
            raise ConnectionError('mock API disconnected')
        if op == 'status' and self.read_failures:
            self.read_failures -= 1
            raise ConnectionError('mock status reply lost')
        value = await OpenSandboxSession._job_rpc(self, op, run_id, argument)
        if op == 'submit' and self.lost_submit_reply:
            self.lost_submit_reply = False
            raise ConnectionError('submission succeeded but reply was lost')
        return value

    async def command(self, command, timeout, *, max_output_bytes=None):
        # Exercise the production shell/RPC boundary; only relocate the image's
        # installed helpers into this test's isolated filesystem.
        args = shlex.split(command)
        assert args[:2] == ['bash', '-c'] and len(args) == 3
        args[2] = args[2].replace('/opt/code-interpreter/code-interpreter-env.sh',
                                 shlex.quote(str(self.bootstrap)))
        args[2] = args[2].replace('/tmp/paperagent_job.py', shlex.quote(str(self.helper)))
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        result = (proc.returncode, out.decode(), err.decode())
        self.rpc_outputs.append(result)
        return result

    async def close(self):
        self.closes += 1
        await super().close()
