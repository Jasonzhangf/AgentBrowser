package com.agentbrowser.probe;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.json.JSONObject;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public final class BrowserNavigationContractTest {
    @Test public void androidProbeAdvertisesNavigationAndKeepsUnsupportedCapabilitiesFalse() throws Exception {
        JSONObject capabilities = NetworkSession.capabilitiesProjection();

        assertTrue("navigation has a wired Android native path", capabilities.getBoolean("navigation"));
        assertFalse("tabs has no Android native owner", capabilities.getBoolean("tabs"));
        assertFalse("profiles has no Android native owner", capabilities.getBoolean("profiles"));
        assertTrue("connectionSetup owns the SAF import path", capabilities.getBoolean("connectionSetup"));
        assertFalse("quality has no Android native owner", capabilities.getBoolean("quality"));
    }

    @Test public void mainActivityForwardsNavigateThroughTheNativeNavigationCommand() throws IOException {
        String activitySource = read(Path.of("apps/android/app/src/main/java/com/agentbrowser/probe/MainActivity.java"));
        String navigationCase = activitySource.substring(
            activitySource.indexOf("case \"navigate\""), activitySource.indexOf("case \"click\""));

        assertTrue("navigate must require op/epoch/url",
            navigationCase.contains("requireFields(value, \"op\", \"epoch\", \"url\")"));
        assertTrue("navigate must dispatch the native navigation command with the URL",
            navigationCase.contains("network.command(NetworkSession.COMMAND_NAVIGATE, value.getLong(\"epoch\"), 0, 0, 0, 0, value.getString(\"url\"))"));
    }

    @Test public void navigationBridgeCommandMatchesTheNativeConnectionOp() {
        assertEquals("NativeConnection op=7 is navigate", 7, NetworkSession.COMMAND_NAVIGATE);
        assertEquals(NetworkSession.COMMAND_NAVIGATE, NetworkSession.navigationBridgeCommand());
    }

    private static String read(Path repositoryRelative) throws IOException {
        Path root = Path.of(System.getProperty("user.dir"));
        while (root != null && !Files.isRegularFile(root.resolve("apps/android/settings.gradle"))) {
            root = root.getParent();
        }
        if (root == null) throw new IOException("REPOSITORY_ROOT_NOT_FOUND");
        return Files.readString(root.resolve(repositoryRelative), StandardCharsets.UTF_8);
    }
}
