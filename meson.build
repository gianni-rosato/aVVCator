project('avvcator',
  version: '0.0.1',
  meson_version: '>= 0.59',
)

dependency('glib-2.0')
dependency('pygobject-3.0')
dependency('libadwaita-1', version: '>= 1.4.0')

gnome = import('gnome')
python = import('python').find_installation('python3')

subdir('data')
subdir('src')

gnome.post_install(
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
