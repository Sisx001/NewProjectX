# Android analysis

## Static

```sh
jadx -d out/ target.apk                 # full decompile to Java
jadx --show-bad-code -d out/ target.apk # keep partially-decompiled methods
apktool d target.apk -o apk_out/        # resources + AndroidManifest + smali
apktool b apk_out/ -o rebuilt.apk       # reassemble after edits
# sign rebuilt apk:
#   keytool/apksigner not installed; use uber-apklinger via gh release if needed
```

Key files after apktool: `apk_out/AndroidManifest.xml`, `apk_out/smali*/`,
`apk_out/res/`, `apk_out/assets/`.

## Dynamic (Frida)

Device/emulator needed (`frida-ls-devices` to list). Default USB device:

```sh
frida-ls-devices
frida-ps -U                     # processes on device
frida -U -f com.example.app -l hook.js --no-pause
```

Hook template:

```javascript
Java.perform(function () {
  var Cls = Java.use("com.example.app.Secret");
  Cls.check.overload("java.lang.String").implementation = function (s) {
    console.log("check arg:", s);
    var r = this.check(s);
    console.log("check ret:", r);
    return r;
  };
});
```

Native hooks: `Interceptor.attach(Module.getBaseAddress("libfoo.so").add(0x1234), {...})`.

## Notes

- No Android emulator is installed in this sandbox; dynamic analysis needs a
  connected device/emulator (adb over network works if reachable).
- Static path (jadx + apktool + rea) works fully offline.
