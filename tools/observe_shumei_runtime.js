'use strict';

// Runtime evidence helper for the user-owned device. Sensitive header values
// are never sent to the host: only their length and SHA-256 digest are emitted.
Java.perform(function () {
  const MessageDigest = Java.use('java.security.MessageDigest');
  const JString = Java.use('java.lang.String');

  function digest(value) {
    if (value === null || value === undefined) return null;
    const bytes = JString.$new(String(value)).getBytes('UTF-8');
    const result = MessageDigest.getInstance('SHA-256').digest(bytes);
    let output = '';
    for (let index = 0; index < result.length; index += 1) {
      output += ('0' + ((result[index] & 255).toString(16))).slice(-2);
    }
    return output;
  }

  function summary(name, value) {
    const text = value === null || value === undefined ? '' : String(value);
    return { name: String(name), length: text.length, sha256: digest(text) };
  }

  const loaded = Java.enumerateLoadedClassesSync().filter(function (name) {
    return /(?:Shumei|DeviceFP|SmAntiFraud)/.test(name);
  });
  send({ event: 'loaded_classes', classes: loaded });

  try {
    const antiFraud = Java.use('com.ishumei.smantifraud.SmAntiFraud');
    const deviceId = antiFraud.getDeviceId.overload().call(antiFraud);
    const vData = antiFraud.getVData.overload().call(antiFraud);
    send({
      event: 'sdk_values',
      device_id: summary('SmAntiFraud.getDeviceId', deviceId),
      vdata: summary('SmAntiFraud.getVData', vData),
      sdk_version: String(antiFraud.getSDKVersion.overload().call(antiFraud)),
    });
  } catch (error) {
    send({ event: 'sdk_error', type: error.$className || error.name || 'Error' });
  }

  try {
    const RequestBuilder = Java.use('okhttp3.Request$Builder');
    const build = RequestBuilder.build.overload();
    build.implementation = function () {
      const request = build.call(this);
      try {
        const url = request.url();
        if (String(url.host()) === 'bff.starbucks.com.cn') {
          const headers = request.headers();
          const selected = [];
          ['Sm-Token', 'x-bs-device-id', 'x-device-id', 'x-app-version', 'x-msr-version'].forEach(function (name) {
            const value = headers.get(name);
            if (value !== null) selected.push(summary(name, value));
          });
          send({ event: 'request', path: String(url.encodedPath()), headers: selected });
        }
      } catch (error) {
        send({ event: 'request_observation_error', type: error.$className || error.name || 'Error' });
      }
      return request;
    };
    send({ event: 'request_hook', installed: true });
  } catch (error) {
    send({ event: 'request_hook', installed: false, type: error.$className || error.name || 'Error' });
  }
});
