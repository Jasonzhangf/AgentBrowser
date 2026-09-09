package com.agentbrowser.probe;

import android.content.Intent;
import android.graphics.Bitmap;
import android.os.SystemClock;
import android.test.InstrumentationTestCase;
import android.webkit.WebView;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Field;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.KeyStore;
import java.security.SecureRandom;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;
import org.json.JSONArray;
import org.json.JSONObject;
import org.json.JSONTokener;

/** Real Cordis account UI -> AccountSession -> JNI -> TLS Relay fixture. */
public final class AccountDeviceTest extends InstrumentationTestCase {
    private static final String USERNAME = "account-alice";
    private static final String PASSWORD = "account-password-123";
    private MainActivity activity;

    private String js(String script) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>();
        getInstrumentation().runOnMainSync(() -> activity.webView.evaluateJavascript(script, value -> {
            result.set(value);
            done.countDown();
        }));
        assertTrue("Account UI JavaScript deadline", done.await(3, TimeUnit.SECONDS));
        return result.get();
    }

    private JSONObject uiStatus() throws Exception {
        String request = JSONObject.quote("{\"op\":\"account_status\"}");
        Object value = new JSONTokener(js("JSON.stringify(JSON.parse(ProbeNative.request(" + request + ")))"))
            .nextValue();
        assertTrue("Account UI status must be a JSON string", value instanceof String);
        return new JSONObject((String) value);
    }

    private JSONObject status() throws Exception {
        AtomicReference<JSONObject> result = new AtomicReference<>();
        AtomicReference<Exception> failure = new AtomicReference<>();
        getInstrumentation().runOnMainSync(() -> {
            try {
                result.set(activity.account.status());
            } catch (Exception error) {
                failure.set(error);
            }
        });
        if (failure.get() != null) throw failure.get();
        return result.get();
    }

    private JSONObject request(JSONObject command) throws Exception {
        AtomicReference<JSONObject> result = new AtomicReference<>();
        AtomicReference<Exception> failure = new AtomicReference<>();
        getInstrumentation().runOnMainSync(() -> {
            try {
                result.set(activity.account.request(command));
            } catch (Exception error) {
                failure.set(error);
            }
        });
        if (failure.get() != null) throw failure.get();
        return result.get();
    }

    private void until(String script, long timeout) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + timeout;
        do {
            if ("true".equals(js(script))) return;
            SystemClock.sleep(100);
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("Account UI condition timed out: " + script + "\n" + js("document.body.innerText"));
    }

    private void untilState(String expected, long timeout) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + timeout;
        JSONObject current = status();
        do {
            if (expected.equals(current.optString("accountState"))) return;
            SystemClock.sleep(100);
            current = status();
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("Account state timed out: expected=" + expected + ", actual=" + current);
    }

    private void untilDirectory(String hostStatus, String directoryState, long timeout) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + timeout;
        JSONObject current = status();
        do {
            JSONArray hosts = current.optJSONArray("hosts");
            if (directoryState.equals(current.optString("directoryState")) && hosts != null && hosts.length() == 1
                && hostStatus.equals(hosts.getJSONObject(0).optString("status"))) return;
            SystemClock.sleep(100);
            current = status();
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("Directory state timed out: expected=" + hostStatus + "/" + directoryState + ", actual=" + current);
    }

    private void refreshUntilDirectory(String hostStatus, String directoryState, long timeout) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + timeout;
        do {
            JSONObject current = status();
            JSONArray hosts = current.optJSONArray("hosts");
            if (directoryState.equals(current.optString("directoryState")) && hosts != null && hosts.length() == 1
                && hostStatus.equals(hosts.getJSONObject(0).optString("status"))) return;
            if (!current.optBoolean("pending") && "authenticated".equals(current.optString("accountState"))) {
                click("account-refresh");
            }
            SystemClock.sleep(100);
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("Directory refresh timed out: expected=" + hostStatus + "/" + directoryState + ", actual=" + status());
    }

    private void untilRegistered(long timeout) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + timeout;
        JSONObject direct = status();
        JSONObject bridge = uiStatus();
        do {
            boolean deviceRegistered = direct.optString("deviceId", "").length() > 0;
            boolean bridgeRegistered = bridge.optString("deviceId", "").length() > 0;
            boolean refreshEnabled = "true".equals(js("!!document.getElementById('account-refresh') && !document.getElementById('account-refresh').disabled"));
            boolean renderedAuthenticated = "true".equals(js("document.querySelector('[data-account-state]')?.dataset.accountState === 'authenticated'"));
            if ("authenticated".equals(direct.optString("accountState"))
                && deviceRegistered
                && "authenticated".equals(bridge.optString("accountState"))
                && bridgeRegistered
                && refreshEnabled
                && renderedAuthenticated) return;
            SystemClock.sleep(100);
            direct = status();
            bridge = uiStatus();
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("Account registration UI timed out: direct=" + direct + ", bridge=" + bridge
            + "\n" + js("JSON.stringify({state:document.querySelector('[data-account-state]')?.dataset.accountState,body:document.body.innerText})"));
    }

    private void untilRenderedAccountState(String expected, long timeout) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + timeout;
        do {
            if ("true".equals(js("document.querySelector('[data-account-state]')?.dataset.accountState === " + JSONObject.quote(expected)))) return;
            SystemClock.sleep(100);
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("Rendered account state timed out: expected=" + expected + ", direct=" + status()
            + ", bridge=" + uiStatus() + "\n" + js("JSON.stringify({state:document.querySelector('[data-account-state]')?.dataset.accountState,body:document.body.innerText})"));
    }

    private void fill(String id, String value) throws Exception {
        js("(()=>{const e=document.getElementById(" + JSONObject.quote(id)
            + ");const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(e,"
            + JSONObject.quote(value) + ");e.dispatchEvent(new Event('input',{bubbles:true}));})()");
    }

    private void click(String id) throws Exception {
        js("document.getElementById(" + JSONObject.quote(id) + ").click()");
    }

    private long nativeHandle() throws Exception {
        Field field = AccountSession.class.getDeclaredField("handle");
        field.setAccessible(true);
        AtomicReference<Long> result = new AtomicReference<>();
        getInstrumentation().runOnMainSync(() -> {
            try {
                result.set(field.getLong(activity.account));
            } catch (IllegalAccessException error) {
                throw new AssertionError(error);
            }
        });
        return result.get();
    }

    private String relayOrigin() throws Exception {
        return new String(Files.readAllBytes(new File(activity.getFilesDir(), "relay/origin.txt").toPath()), StandardCharsets.UTF_8).trim();
    }

    private byte[] relayFile(String name) throws Exception {
        return Files.readAllBytes(new File(activity.getFilesDir(), "relay/" + name).toPath());
    }

    private void assertWrongCaRejected() throws Exception {
        NativeConnection.load();
        long handle = 0;
        try {
            handle = NativeAccount.login(relayOrigin(), relayFile("wrong-ca.der"), USERNAME, PASSWORD);
            fail("Wrong Relay CA unexpectedly authenticated");
        } catch (IllegalStateException expected) {
            assertTrue("Wrong CA failure must remain explicit", expected.toString().contains("relay"));
        } finally {
            if (handle != 0) NativeAccount.close(handle);
        }
    }

    private void fixtureControl(String path) throws Exception {
        String controlUrl = ((android.test.InstrumentationTestRunner) getInstrumentation()).getArguments().getString("accountControlUrl");
        assertNotNull("Dedicated account fixture control URL", controlUrl);
        CertificateFactory factory = CertificateFactory.getInstance("X.509");
        X509Certificate ca = (X509Certificate) factory.generateCertificate(new ByteArrayInputStream(relayFile("ca.der")));
        KeyStore store = KeyStore.getInstance(KeyStore.getDefaultType());
        store.load(null, null);
        store.setCertificateEntry("account-fixture-ca", ca);
        TrustManagerFactory managers = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        managers.init(store);
        SSLContext tls = SSLContext.getInstance("TLS");
        tls.init(null, managers.getTrustManagers(), new SecureRandom());
        HttpsURLConnection connection = (HttpsURLConnection) new URL(controlUrl + path).openConnection();
        connection.setSSLSocketFactory(tls.getSocketFactory());
        connection.setRequestMethod("POST");
        connection.setConnectTimeout(5000);
        connection.setReadTimeout(5000);
        assertEquals("Account fixture control", 200, connection.getResponseCode());
        connection.disconnect();
    }

    private void capture(String name) throws Exception {
        js("window.scrollTo(0,document.body.scrollHeight);true");
        SystemClock.sleep(150);
        Bitmap screenshot = getInstrumentation().getUiAutomation().takeScreenshot();
        assertNotNull("Account screenshot", screenshot);
        File directory = new File(activity.getFilesDir(), "account-evidence");
        assertTrue(directory.isDirectory() || directory.mkdirs());
        try (FileOutputStream output = new FileOutputStream(new File(directory, name + ".png"))) {
            assertTrue(screenshot.compress(Bitmap.CompressFormat.PNG, 100, output));
        }
    }

    private void writeResult(JSONObject result) throws Exception {
        File directory = new File(activity.getFilesDir(), "account-evidence");
        assertTrue(directory.isDirectory() || directory.mkdirs());
        try (FileOutputStream output = new FileOutputStream(new File(directory, "result.json"))) {
            output.write(result.toString(2).getBytes(StandardCharsets.UTF_8));
        }
    }

    private void startActivity() throws Exception {
        Intent intent = new Intent(getInstrumentation().getTargetContext(), MainActivity.class)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        activity = (MainActivity) getInstrumentation().startActivitySync(intent);
        until("!!document.getElementById('account-login')", 10000);
    }

    private void finishActivity() {
        if (activity != null) {
            getInstrumentation().runOnMainSync(() -> activity.finish());
            activity = null;
        }
    }

    public void testAccountScenario() throws Exception {
        String scenario = ((android.test.InstrumentationTestRunner) getInstrumentation()).getArguments().getString("accountScenario", "directory");
        if ("revoke-failure".equals(scenario)) runRevokeFailure();
        else runDirectoryAndLifecycle();
    }

    private void runDirectoryAndLifecycle() throws Exception {
        startActivity();
        try {
            assertWrongCaRejected();
            fill("account-username", USERNAME);
            fill("account-password", "wrong-password-123");
            click("account-login");
            untilState("error", 15000);
            assertTrue("Wrong credentials remain visible as an error", status().optString("error").contains("401"));
            untilRenderedAccountState("error", 15000);
            until("!!document.getElementById('account-password')", 15000);
            assertEquals("Password input is cleared after login request", "true", js("document.getElementById('account-password').value===''"));
            assertEquals("Password never becomes UI text", "false", js("document.body.innerText.includes(" + JSONObject.quote(PASSWORD) + ")"));

            fill("account-password", PASSWORD);
            click("account-login");
            untilState("authenticated", 15000);
            untilRenderedAccountState("authenticated", 15000);

            until("!!document.getElementById('account-register')", 3000);
            fill("account-device-name", "15T-account-test");
            click("account-register");
            long registerDeadline = SystemClock.elapsedRealtime() + 15000;
            do {
                if (status().optString("deviceId", "").length() > 0) break;
                SystemClock.sleep(100);
            } while (SystemClock.elapsedRealtime() < registerDeadline);
            assertTrue("Real Relay device registration", status().optString("deviceId", "").length() > 0);

            untilRegistered(15000);
            click("account-refresh");
            untilDirectory("online", "fresh", 15000);
            capture("directory-online");

            fixtureControl("/drop-host");
            refreshUntilDirectory("offline", "fresh", 15000);
            capture("directory-offline");

            SystemClock.sleep(30_500);
            untilDirectory("expired", "expired", 5000);
            until("document.body.innerText.includes('目录已过期')", 15000);
            capture("directory-expired");

            for (int attempt = 0; attempt < 5; attempt++) {
                request(new JSONObject().put("op", "account_login").put("username", USERNAME).put("password", PASSWORD));
                request(new JSONObject().put("op", "account_logout"));
            }
            untilState("signed_out", 15000);
            SystemClock.sleep(1500);
            assertEquals("Stale login callbacks cannot restore a session", "signed_out", status().getString("accountState"));

            request(new JSONObject().put("op", "account_login").put("username", USERNAME).put("password", PASSWORD));
            untilState("authenticated", 15000);
            long releasedHandle = nativeHandle();
            assertTrue("AccountSession owns a live native handle", releasedHandle > 0);
            getInstrumentation().runOnMainSync(() -> activity.account.close());
            JSONObject closed = status();
            assertEquals("AccountSession close signs out after native cleanup", "signed_out", closed.getString("accountState"));
            assertEquals("Successful close has no error", JSONObject.NULL, closed.get("error"));
            try {
                NativeAccount.status(releasedHandle);
                fail("Closed AccountSession handle remained usable");
            } catch (IllegalStateException expected) { }

            finishActivity();
            startActivity();
            try {
                request(new JSONObject().put("op", "account_login").put("username", USERNAME).put("password", PASSWORD));
                untilState("authenticated", 15000);
                long externallyClosed = nativeHandle();
                NativeAccount.close(externallyClosed);
                getInstrumentation().runOnMainSync(() -> activity.account.close());
                JSONObject closeFailure = status();
                assertEquals("Close failure is not reported as signed out success", "error", closeFailure.getString("accountState"));
                assertTrue("Close failure remains visible at AccountSession owner", closeFailure.getString("error").contains("ACCOUNT_NATIVE_CLOSE_FAILED"));
                capture("close-error");
                writeResult(new JSONObject()
                    .put("directoryOnline", true)
                    .put("directoryOffline", true)
                    .put("directoryExpired", true)
                    .put("wrongCaRejected", true)
                    .put("wrongCredentialsRejected", true)
                    .put("passwordCleared", true)
                    .put("deviceRegistered", true)
                    .put("staleCallbacksFenced", true)
                    .put("closeReleasedHandle", true)
                    .put("closeFailureVisible", true)
                    .put("accountStateAfterCloseFailure", closeFailure.getString("accountState")));
            } finally {
                finishActivity();
            }
        } finally {
            finishActivity();
        }
    }

    private void runRevokeFailure() throws Exception {
        startActivity();
        try {
            fill("account-username", USERNAME);
            fill("account-password", PASSWORD);
            click("account-login");
            untilState("authenticated", 15000);
            untilRenderedAccountState("authenticated", 15000);
            click("account-logout");
            long deadline = SystemClock.elapsedRealtime() + 15000;
            JSONObject current = status();
            do {
                if ("signed_out".equals(current.optString("accountState"))
                    && current.optString("error", "").contains("RELAY_REVOKE_UNCONFIRMED")) break;
                SystemClock.sleep(100);
                current = status();
            } while (SystemClock.elapsedRealtime() < deadline);
            assertEquals("Local logout completes", "signed_out", current.getString("accountState"));
            assertTrue("Remote revoke failure is explicit", current.getString("error").contains("RELAY_REVOKE_UNCONFIRMED"));
            until("document.body.innerText.includes('RELAY_REVOKE_UNCONFIRMED')", 15000);
            capture("revoke-warning");
            writeResult(new JSONObject().put("localLogout", true).put("remoteRevokeUnconfirmed", true).put("warningVisible", true));
        } finally {
            finishActivity();
        }
    }
}
