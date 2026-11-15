// Builds the water wallpaper prototype into a portable folder:
//   dist/water.html                (single self-contained page: JS inlined, no server needed)
//   dist/Launch-WaterWallpaper.ps1 (reads the wallpaper, opens Edge fullscreen)
//   dist/Launch-WaterWallpaper.cmd (double-click wrapper)
// plus dist/../water-wallpaper.zip with the same three files, for copying to a Windows machine.
//
// Run from client/:  npm run build:wallpaper
import { build } from 'vite'
import { execFileSync } from 'node:child_process'
import { chmodSync, copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const tmp = join(here, '.build')
const dist = join(here, 'dist')

await build({
  configFile: false,
  logLevel: 'warn',
  root: here,
  build: {
    outDir: tmp,
    emptyOutDir: true,
    minify: true,
    lib: { entry: join(here, 'main.ts'), formats: ['iife'], name: 'WaterWallpaper', fileName: () => 'app.js' },
  },
})

const js = readFileSync(join(tmp, 'app.js'), 'utf8').replaceAll('</script', '<\\/script')
const html = readFileSync(join(here, 'index.html'), 'utf8').replace('<!--APP-->', `<script>\n${js}\n</script>`)
rmSync(dist, { recursive: true, force: true })
mkdirSync(dist)
writeFileSync(join(dist, 'water.html'), html)
copyFileSync(join(here, 'Launch-WaterWallpaper.ps1'), join(dist, 'Launch-WaterWallpaper.ps1'))
copyFileSync(join(here, 'launch-water-wallpaper.sh'), join(dist, 'launch-water-wallpaper.sh'))
chmodSync(join(dist, 'launch-water-wallpaper.sh'), 0o755)
// Windows line endings for the batch file.
writeFileSync(join(dist, 'Launch-WaterWallpaper.cmd'),
  '@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-WaterWallpaper.ps1"\r\nif errorlevel 1 pause\r\n')
rmSync(tmp, { recursive: true, force: true })

const zip = join(here, 'water-wallpaper.zip')
rmSync(zip, { force: true })
try {
  execFileSync('zip', ['-j', '-q', zip, join(dist, 'water.html'), join(dist, 'Launch-WaterWallpaper.ps1'), join(dist, 'Launch-WaterWallpaper.cmd'), join(dist, 'launch-water-wallpaper.sh')])
  console.log(`Built ${dist} and ${zip}`)
} catch {
  console.log(`Built ${dist} (zip not available, copy the folder instead)`)
}
