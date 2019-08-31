"""An example of how to setup and start an Accessory.
This is:
1. Create the Accessory object you want.
2. Add it to an AccessoryDriver, which will advertise it on the local network,
    setup a server to answer client queries, etc.
"""
import asyncio
import logging
import os
import signal
import subprocess

from pyhap.accessory_driver import AccessoryDriver
from pyhap import camera

logging.basicConfig(level=logging.DEBUG, format="[%(module)s] %(message)s")
logger = logging.getLogger('main')

FFMPEG_CMD = (
    # pylint: disable=bad-continuation
    "raspivid -n -ih -t 0 -ex auto -w {width} -h {height} -fps {fps} "
    " -b {v_max_bitrate} -o - "
    "| ffmpeg -i - -c:v copy "
    "-payload_type 99 -ssrc {v_ssrc} -f rtp "
    "-srtp_out_suite AES_CM_128_HMAC_SHA1_80 -srtp_out_params {v_srtp_key} "
    "'srtp://{address}:{v_port}?rtcpport={v_port}&"
    "localrtcpport={v_port}&pkt_size=1378'"
)
'''Template for the ffmpeg command.'''

class ClassName(object):
    """docstring for ClassName"""
    def __init__(self, arg):
        super(ClassName, self).__init__()
        self.arg = arg


class PiCamera(camera.Camera):
    """A camera that implements the capture function"""
    def __init__(self, *args, **kwargs):
        super(PiCamera, self).__init__(*args, **kwargs)
        self.logger = logging.getLogger('PiCamera')

    def get_snapshot(self, image_size):  # pylint: disable=no-self-use
        cmd = ["raspistill",
                "-n", # No preview
                "-t", "2000", # 2 seconds to warm up
                "-ex", "auto", # auto exposure
                "-mm", "average", # metering mode, average
                "-drc", "med", # do some dynamic range compression
                "-w", str(image_size["image-width"]),
                "-h", str(image_size["image-height"]),
                "-o", "-"]
        self.logger.debug("Executing image capture command: %s", ' '.join(cmd))
        raspistill = subprocess.run(["raspistill",
                                     "-n", # No preview
                                     "-t", "2000", # 2 seconds to warm up
                                     "-ex", "auto", # auto exposure
                                     "-mm", "average", # metering mode, average
                                     "-drc", "med", # do some dynamic range compression
                                     "-w", str(image_size["image-width"]),
                                     "-h", str(image_size["image-height"]),
                                     "-o", "-"], # output to stdout
                                     capture_output=True) # ensure we grab stdout
        output = raspistill.stderr.decode('utf-8').strip()
        if output:
            self.logger.error("Error in still output: %s", output)
        return raspistill.stdout

    async def start_stream(self, session_info, stream_config):
        stream_config['v_max_bitrate'] *= 1000 # kbps to bps conversion

        self.logger.debug('[%s] Starting stream with the following parameters: %s',
                      session_info['id'], stream_config)

        cmd = self.start_stream_cmd.format(**stream_config)
        self.logger.debug('Executing start stream command: "%s"', cmd)
        try:
            process = await asyncio.create_subprocess_shell(cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    limit=1024,
                    start_new_session=True)
        except Exception as e:  # pylint: disable=broad-except
            self.logger.error('Failed to start streaming process because of error: %s', e)
            return False

        session_info['process'] = process

        self.logger.info('[%s] Started stream process - PID %d',
                     session_info['id'], process.pid)

        return True

    async def stop_stream(self, session_info):  # pylint: disable=no-self-use
        """Stop the stream for the given ``session_id``.

        This method can be implemented if custom stop stream commands are needed. The
        default implementation gets the ``process`` value from the ``session_info``
        object and terminates it (assumes it is a ``subprocess.Popen`` object).

        :param session_info: The session info object. Available keys:
            - id - The session ID.
        :type session_info: ``dict``
        """
        session_id = session_info['id']
        ffmpeg_process = session_info.get('process')
        pgid = os.getpgid(ffmpeg_process.pid)
        if ffmpeg_process:
            self.logger.info('[%s] Stopping stream.', session_id)
            try:
                os.killpg(pgid, signal.SIGTERM)
                _, stderr = await asyncio.wait_for(
                    ffmpeg_process.communicate(), timeout=2.0)
                self.logger.debug('Stream command stderr: %s', stderr)
            except asyncio.TimeoutError:
                self.logger.error('Timeout while waiting for the stream process '
                                  'to terminate. Trying with kill.')
                os.killpg(pgid, signal.SIGKILL)
                await ffmpeg_process.wait()
            self.logger.debug('Stream process stopped.')
        else:
            self.logger.warning('No process for session ID %s', session_id)


# Specify the audio and video configuration that your device can support
# The HAP client will choose from these when negotiating a session.
options = {
    "video": {
        "codec": {
            "profiles": [
                camera.VIDEO_CODEC_PARAM_PROFILE_ID_TYPES["BASELINE"],
                camera.VIDEO_CODEC_PARAM_PROFILE_ID_TYPES["MAIN"],
                camera.VIDEO_CODEC_PARAM_PROFILE_ID_TYPES["HIGH"]
            ],
            "levels": [
                camera.VIDEO_CODEC_PARAM_LEVEL_TYPES['TYPE3_1'],
                camera.VIDEO_CODEC_PARAM_LEVEL_TYPES['TYPE3_2'],
                camera.VIDEO_CODEC_PARAM_LEVEL_TYPES['TYPE4_0'],
            ],
        },
        "resolutions": [
            # Width, Height, framerate
            [352, 240, 15], # Required for Apple Watch
            [1920, 1080, 30],
            [1280, 720, 30],
            [854, 480, 30],
            [480, 360, 30],
        ],
    },
    "audio": {
        "codecs": [ ],
    },
    "srtp": True,
    "address": "172.24.0.30",
    "start_stream_cmd": FFMPEG_CMD,
}


# Start the accessory on port 51826
driver = AccessoryDriver(port=51826)
acc = PiCamera(options, driver, "Camera")
driver.add_accessory(accessory=acc)

# We want KeyboardInterrupts and SIGTERM (terminate) to be handled by the driver itself,
# so that it can gracefully stop the accessory, server and advertising.
signal.signal(signal.SIGTERM, driver.signal_handler)
# Start it!
driver.start()
