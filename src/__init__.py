import logging
import sys
import threading
import subprocess
import gi
import json
import os
import time
import shutil
from ffmpeg_progress_yield import FfmpegProgress

from pathlib import Path

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio

Adw.init()

from . import info

BASE_DIR = Path(__file__).resolve().parent

def humanize(seconds):
    seconds = round(seconds)
    words = ["year", "day", "hour", "minute", "second"]

    if not seconds:
        return "now"
    else:
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        d, h = divmod(h, 24)
        y, d = divmod(d, 365)

        time = [y, d, h, m, s]

        duration = []

        for x, i in enumerate(time):
            if i == 1:
                duration.append(f"{i} {words[x]}")
            elif i > 1:
                duration.append(f"{i} {words[x]}s")

        if len(duration) == 1:
            return duration[0]
        elif len(duration) == 2:
            return f"{duration[0]} and {duration[1]}"
        else:
            return ", ".join(duration[:-1]) + " and " + duration[-1]

def notify(text):
    application = Gtk.Application.get_default()
    notification = Gio.Notification.new(title="avvcator")
    notification.set_body(text)
    application.send_notification(None, notification)


def first_open():
    startup_file = os.path.join(Path.home(), ".var/app/net.natesales.aVVCator/startup.dat")
    if os.path.exists(startup_file):
        return False
    else:
        with open(startup_file, "w") as f:
            f.write("\n")
        return True


class FileSelectDialog(Gtk.FileChooserDialog):
    home = Path.home()

    def __init__(self, parent, select_multiple, label, selection_text, open_only, callback=None):
        super().__init__(transient_for=parent, use_header_bar=True)
        self.select_multiple = select_multiple
        self.label = label
        self.callback = callback
        self.set_action(action=Gtk.FileChooserAction.OPEN if open_only else Gtk.FileChooserAction.SAVE)
        self.set_title(title="Select " + selection_text + " files" if self.select_multiple else "Select " + selection_text + " file")
        self.set_modal(modal=True)
        self.set_select_multiple(select_multiple=self.select_multiple)
        self.connect("response", self.dialog_response)
        self.set_current_folder(Gio.File.new_for_path(path=str(self.home)))

        self.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL,
            "_Select", Gtk.ResponseType.OK
        )
        btn_select = self.get_widget_for_response(response_id=Gtk.ResponseType.OK)
        btn_select.get_style_context().add_class(class_name="suggested-action")
        btn_cancel = self.get_widget_for_response(response_id=Gtk.ResponseType.CANCEL)
        btn_cancel.get_style_context().add_class(class_name="destructive-action")

        self.show()

    def dialog_response(self, widget, response):
        if response == Gtk.ResponseType.OK:
            if self.select_multiple:
                gliststore = self.get_files()
                for glocalfile in gliststore:
                    print(glocalfile.get_path())
            else:
                glocalfile = self.get_file()
                # print(glocalfile.get_path())
                self.label.set_label(glocalfile.get_path())
        if self.callback is not None:
            self.callback()
        widget.close()

@Gtk.Template(filename=str(BASE_DIR.joinpath('startup.ui')))
class OnboardWindow(Adw.Window):
    __gtype_name__ = "OnboardWindow"

    image = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.image.set_from_file(
            filename=str(
                BASE_DIR.joinpath('net.natesales.avvcator-splash.png')
            )
        )

    @Gtk.Template.Callback()
    def go(self, button):
        app.win = MainWindow(application=app)
        app.win.present()
        self.destroy()


@Gtk.Template(filename=str(BASE_DIR.joinpath("window.ui")))
class MainWindow(Adw.Window):
    __gtype_name__ = "avvcatorWindow"

    # Video page
    source_file_label = Gtk.Template.Child()
    resolution_width_entry = Gtk.Template.Child()
    resolution_height_entry = Gtk.Template.Child()
    crop_toggle = Gtk.Template.Child()
    crf_scale = Gtk.Template.Child()
    speed_slower_button = Gtk.Template.Child()
    speed_slow_button = Gtk.Template.Child()
    speed_medium_button = Gtk.Template.Child()
    speed_fast_button = Gtk.Template.Child()
    speed_faster_button = Gtk.Template.Child()
    speed_string = "medium"

    # Audio page
    bitrate_entry = Gtk.Template.Child()
    downmix_switch = Gtk.Template.Child()
    audio_copy_switch = Gtk.Template.Child()
    info_copy_audio = Gtk.Template.Child()
    loudnorm_toggle = Gtk.Template.Child()
    volume_scale = Gtk.Template.Child()

    # Export page
    output_file_label = Gtk.Template.Child()
    encode_button = Gtk.Template.Child()
    encoding_spinner = Gtk.Template.Child()
    stop_button = Gtk.Template.Child()
    progress_bar = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Default to speed medium
        self.speed_faster_button.set_has_frame(False)
        self.speed_fast_button.set_has_frame(False)
        self.speed_medium_button.set_has_frame(True)
        self.speed_slow_button.set_has_frame(False)
        self.speed_slower_button.set_has_frame(False)
        self.speed_string = "medium"

        # Reset value to remove extra decimal
        self.crf_scale.set_value(0)
        self.crf_scale.set_value(24)
        self.volume_scale.set_value(0)
        self.volume_scale.set_value(6)
        self.volume_scale.set_value(0)

        # resolution and audio bitrate
        self.metadata: (float, float, float) = ()

        # Absolute source path file
        self.source_file_absolute = ""
        self.output_file_absolute = ""

        # Set progress bar to 0
        self.progress_bar.set_fraction(0)
        self.progress_bar.set_text("0%")
        self.process = None
        self.encode_start = None

    def load_metadata(self):
        self.metadata = metadata(self.source_file_absolute)

    @Gtk.Template.Callback()
    def empty_or_not_empty(self, switch, gboolean):
        if self.audio_copy_switch.get_state():
            self.bitrate_entry.set_sensitive(True)
            self.audio_copy_switch.set_sensitive(True)
            self.loudnorm_toggle.set_sensitive(True)
            self.volume_scale.set_sensitive(True)
            self.downmix_switch.set_sensitive(True)
        else:
            self.bitrate_entry.set_sensitive(False)
            self.loudnorm_toggle.set_sensitive(False)
            self.volume_scale.set_sensitive(False)
            self.downmix_switch.set_sensitive(False)

    def handle_file_select(self):
        # Trim file path
        if "/" in self.source_file_label.get_text():
            self.source_file_absolute = self.source_file_label.get_text()
            self.source_file_label.set_text(os.path.basename(self.source_file_absolute))

    # Video

    @Gtk.Template.Callback()
    def open_source_file(self, button):
        self.bitrate_entry.set_text(str(192))
        FileSelectDialog(
            parent=self,
            select_multiple=False,
            label=self.source_file_label,
            selection_text="source",
            open_only=True,
            callback=self.handle_file_select
        )
    
    @Gtk.Template.Callback()
    def speed_slower(self, button):
        self.speed_faster_button.set_has_frame(False)
        self.speed_fast_button.set_has_frame(False)
        self.speed_medium_button.set_has_frame(False)
        self.speed_slow_button.set_has_frame(False)
        self.speed_slower_button.set_has_frame(True)
        self.speed_string = "slower"

    @Gtk.Template.Callback()
    def speed_slow(self, button):
        self.speed_faster_button.set_has_frame(False)
        self.speed_fast_button.set_has_frame(False)
        self.speed_medium_button.set_has_frame(False)
        self.speed_slow_button.set_has_frame(True)
        self.speed_slower_button.set_has_frame(False)
        self.speed_string = "slow"

    @Gtk.Template.Callback()
    def speed_medium(self, button):
        self.speed_faster_button.set_has_frame(False)
        self.speed_fast_button.set_has_frame(False)
        self.speed_medium_button.set_has_frame(True)
        self.speed_slow_button.set_has_frame(False)
        self.speed_slower_button.set_has_frame(False)
        self.speed_string = "medium"

    @Gtk.Template.Callback()
    def speed_fast(self, button):
        self.speed_faster_button.set_has_frame(False)
        self.speed_fast_button.set_has_frame(True)
        self.speed_medium_button.set_has_frame(False)
        self.speed_slow_button.set_has_frame(False)
        self.speed_slower_button.set_has_frame(False)
        self.speed_string = "fast"

    @Gtk.Template.Callback()
    def speed_faster(self, button):
        self.speed_faster_button.set_has_frame(True)
        self.speed_fast_button.set_has_frame(False)
        self.speed_medium_button.set_has_frame(False)
        self.speed_slow_button.set_has_frame(False)
        self.speed_slower_button.set_has_frame(False)
        self.speed_string = "faster"

    # Export

    @Gtk.Template.Callback()
    def open_output_file(self, button):
        FileSelectDialog(
            parent=self,
            select_multiple=False,
            label=self.output_file_label,
            selection_text="output",
            open_only=False,
        )

    def report_encode_finish(self,encode_start):
        encode_end = time.time() - encode_start
        notify(f"Encode finished in {humanize(encode_end)}! ✈️")
        self.progress_bar.set_fraction(0)
        self.progress_bar.set_text("Encode Finished ~ 0%")
        self.stop_button.set_visible(False)

        self.encode_button.set_visible(True)
        self.encoding_spinner.set_visible(False)

    @Gtk.Template.Callback()
    def start_export(self, button):
        self.encode_button.set_visible(False)
        self.encoding_spinner.set_visible(True)
        self.stop_button.set_visible(True)

        output = self.output_file_label.get_text()
        if not output.endswith(".mp4"):
            output += ".mp4"

        def run_in_thread():           
            width = height = None

            try:
                width = int(self.resolution_width_entry.get_text())
            except ValueError:
                pass

            try:
                height = int(self.resolution_height_entry.get_text())
            except ValueError:
                pass
            
            if self.crop_toggle.get_active():
                if width is not None and height is None:
                    height = "ih"
                elif width is None and height is not None:
                    width = "iw"
            else:
                if width is not None and height is None:
                    height = -2
                elif width is None and height is not None:
                    width = -2

            method = "bicubic:param0=0:param1=1/2"

            if width is not None and height is not None:
                resolution = "crop" + f"={width}:{height}" if self.crop_toggle.get_active() else "scale" + f"={width}:{height}:flags={method}"
            else:
                resolution = "-y"

            if self.audio_copy_switch.get_state():
                audio_filters = "-y"
            else:
                if self.volume_scale.get_value() == 0:
                    if self.loudnorm_toggle.get_active():
                        audio_filters = "loudnorm"
                    else:
                        audio_filters = "-y"
                else:
                    if self.loudnorm_toggle.get_active():
                        audio_filters = f"loudnorm,volume={int(self.volume_scale.get_value())}dB"
                    else:
                        audio_filters = f"volume={int(self.volume_scale.get_value())}dB"

            if self.audio_copy_switch.get_state():
                audio_filters_prefix = "-y"
            else:
                if self.volume_scale.get_value() == 0:
                    if self.loudnorm_toggle.get_active():
                        audio_filters_prefix = "-af"
                    else:
                        audio_filters_prefix = "-y"
                else:
                    if self.loudnorm_toggle.get_active():
                        audio_filters_prefix = "-af"
                    else:
                        audio_filters_prefix = "-af"

            speed_int = 2
            if self.speed_string == "faster":
                speed_int = 0
            if self.speed_string == "fast":
                speed_int = 1
            if self.speed_string == "medium":
                speed_int = 2
            if self.speed_string == "slow":
                speed_int = 3
            if self.speed_string == "slower":
                speed_int = 4
            
            cmd = [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel", "info",
                "-y",
                "-i", self.source_file_absolute,
                "-vf" if width is not None and height is not None else "-y",
                resolution,
                "-map", "0:v",
                "-c:v", "libvvenc",
                "-qp", str(int(self.crf_scale.get_value())),
                "-preset", str(int(speed_int)),
                "-period", "10",
                "-map", "0:a?",
                "-c:a", "copy" if self.audio_copy_switch.get_state() else "aac",
                "-b:a", self.bitrate_entry.get_text() + "K",
                audio_filters_prefix,
                audio_filters,
                "-ac", "2" if self.downmix_switch.get_state() else "0",
                "-map", "-0:s",
                "-c:s", "copy",
                "-metadata", "comment=\"Encoded with aVVCator\"",
                output,
            ]

            print(cmd)
            self.encode_start = time.time()
            self.process = FfmpegProgress(cmd)
            for progress in self.process.run_command_with_progress():
                print(f"{progress}/100")
                self.progress_bar.set_fraction(progress/100)
                self.progress_bar.set_text(f"Encoding ~ {int(progress)}%")
            self.report_encode_finish(self.encode_start)

        thread = threading.Thread(target=run_in_thread)
        thread.start()

    @Gtk.Template.Callback()
    def stop_encode(self, button):
        print("Killing encoding job...")
        if self.process is not None:
            self.process.quit()
            print("Killed encoding job")
            self.report_encode_finish(self.encode_start)

class App(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connect("activate", self.on_activate)

        about_action = Gio.SimpleAction(name="about")
        about_action.connect("activate", self.about_dialog)
        self.add_action(about_action)

        quit_action = Gio.SimpleAction(name="quit")
        quit_action.connect("activate", self.quit)
        self.add_action(quit_action)

    def on_activate(self, app):
        if first_open():
            startup_window = OnboardWindow(application=self)
            startup_window.present()
        else:
            self.win = MainWindow(application=app)
            self.win.present()

    def about_dialog(self, action, user_data):
        about = Adw.AboutWindow(transient_for=self.win,
                                application_name="aVVCator",
                                application_icon="net.natesales.aVVCator",
                                developer_name="Gianni Rosato",
                                version=info.version,
                                copyright="Copyright © 2024 Nate Sales &amp; Gianni Rosato",
                                license_type=Gtk.License.GPL_3_0,
                                website="https://github.com/gianni-rosato/avvcator",
                                issue_url="https://github.com/gianni-rosato/avvcator/issues")
        # about.set_translator_credits(translators())
        about.set_developers(["Nate Sales <nate@natesales.net>","Gianni Rosato <grosatowork@proton.me>","Trix<>"])
        about.set_designers(["Gianni Rosato <grosatowork@proton.me>"])
        about.add_acknowledgement_section(
            ("Special thanks to the encoding community!"),
            [
                "AV1 For Dummies https://discord.gg/bbQD5MjDr3", "H.266 / Versatile Video Coding Discord https://discord.gg/vHMG7tyey3", "Codec Wiki https://wiki.x266.mov/"
            ]
        )
        about.add_legal_section(
            title='FFmpeg',
            copyright='Copyright © 2024 FFmpeg',
            license_type=Gtk.License.GPL_3_0,
        )
        about.add_legal_section(
            title='VVenC',
            copyright='Copyright © 2024 Fraunhofer-Gesellschaft zur Förderung der angewandten Forschung e.V. &amp; The VVenC Authors',
            license_type=Gtk.License.BSD,
        )
        about.present()

    def quit(self, action=None, user_data=None):
        exit()


app = App(application_id="net.natesales.aVVCator")
app.run(sys.argv)
