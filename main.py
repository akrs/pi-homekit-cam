"""Main entry point

We setup a custom subclass to interface with the Pi camera better, and then
start it just like the docs.
"""
import asyncio
import logging
import os
import signal
import subprocess

from pyhap.accessory_driver import AccessoryDriver
from pyhap import camera

logging.basicConfig(level=logging.INFO, format="[%(module)s] %(message)s")
logger = logging.getLogger('main')

STREAM_CMD = (
    # Use raspivid, as it can take advantage of the Pi's h264 encoding hardware
    "raspivid -n " # No preview
    "-ih " # Insert PPS, SPS headers - needed for FFMPEG
    "-t 0 " # run forever
    "-ex auto " # auto expose
    "-drc med " # do some dynamic range compression
    "-w {width} -h {height} -fps {fps} " # set width, height, fps
    "-lev 4 -pf {profile} -b {v_max_bitrate} " # setup the h.264 parameters
    "-o - " # Dump to stdout
    # ffmpeg, does all the hairy STRP stuff
    "| ffmpeg -re -i - -c:v copy "
    "-payload_type 99 -ssrc {v_ssrc} -f rtp "
    "-srtp_out_suite AES_CM_128_HMAC_SHA1_80 -srtp_out_params {v_srtp_key} "
    "'srtp://{address}:{v_port}?rtcpport={v_port}&"
    "localrtcpport={v_port}&pkt_size=1378'"
)
'''Template for the command.'''


class PiCamera(camera.Camera):
    """A camera that implements the capture function"""
    def __init__(self, *args, **kwargs):
        super(PiCamera, self).__init__(*args, **kwargs)
        self.logger = logging.getLogger('PiCamera')

    def get_snapshot(self, image_size):
        """Use the raspistill command to capture a snapshot.
        """
        cmd = ["raspistill",
                "-n", # No preview
                "-t", "200", # 200 milliseconds to warm up
                "-ex", "auto", # auto exposure
                "-mm", "average", # metering mode, average
                "-drc", "med", # do some dynamic range compression
                "-w", str(image_size["image-width"]), # width
                "-h", str(image_size["image-height"]), # height
                "-o", "-"] # output to stdout
        self.logger.debug("Executing image capture command: %s", ' '.join(cmd))
        raspistill = subprocess.run(cmd, capture_output=True) # ensure we grab stdout
        output = raspistill.stderr.decode('utf-8').strip()
        if output:
            self.logger.error("Error in still output: %s", output)
        return raspistill.stdout

    async def start_stream(self, session_info, stream_config):
        """Start the stream.

        Overriding this is necessary beecuse we use a shell to pipe data between
        raspivid and ffmpeg, which means we need a process group and
        create_process_shell instead of create_process_exec
        """
        stream_config['v_max_bitrate'] *= 1000 # kbps to bps conversion
        stream_config['profile'] = ('baseline', 'main', 'high')[ord(stream_config['v_profile_id'])]

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
        except Exception as e:
            self.logger.error('Failed to start streaming process because of error: %s', e)
            return False

        session_info['process'] = process

        self.logger.info('[%s] Started stream process - PID %d',
                     session_info['id'], process.pid)

        return True

    async def stop_stream(self, session_info):  # pylint: disable=no-self-use
        """Stop the stream for the given ``session_id``.

        We implement this because we need to stop the whole process group, not
        just one process.
        """
        session_id = session_info['id']
        ffmpeg_process = session_info.get('process')
        if ffmpeg_process:
            pgid = os.getpgid(ffmpeg_process.pid)
            self.logger.info('[%s] Stopping stream.', session_id)
            try:
                os.killpg(pgid, signal.SIGTERM)
                _, stderr = await asyncio.wait_for(
                    ffmpeg_process.communicate(), timeout=2.0)
                self.logger.debug('Stream command stderr: %s', stderr.decode('utf-8'))
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
                camera.VIDEO_CODEC_PARAM_LEVEL_TYPES['TYPE4_0']
            ],
        },
        "resolutions": [
            # Width, Height, framerate
            [1920, 1080, 25], # 16x9
            [1280, 720, 25],
            [640, 360, 25],
            [480, 270, 25],
            [480, 270, 25],
            [320, 180, 25],
            [1280, 960, 25], # 4:3
            [1024, 768, 25],
            [640, 480, 25],
            [480, 360, 25],
            [320, 240, 25],
        ],
    },
    "audio": { #audio is left blank, because I don't have a mic hooked up.
        "codecs": [ ],
    },
    "srtp": True,
    "address": "172.24.0.30",
    "start_stream_cmd": STREAM_CMD,
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
