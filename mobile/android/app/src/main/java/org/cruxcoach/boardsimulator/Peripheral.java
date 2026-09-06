package org.cruxcoach.boardsimulator;

import android.annotation.SuppressLint;
import android.bluetooth.*;
import android.bluetooth.le.*;
import android.content.*;
import android.os.ParcelUuid;
import org.json.*;
import java.util.*;

/** All mutable transport state lives on the Activity main thread. */
@SuppressLint("MissingPermission") // Activity checks both runtime permissions before start.
final class Peripheral {
    private static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    private static final String STATE = "0000fff4-0000-1000-8000-00805f9b34fb";
    private static final String NOTIFY = "0000fff1-0000-1000-8000-00805f9b34fb";
    private final MainActivity activity;
    private BluetoothAdapter adapter;
    private BluetoothGattServer server;
    private BluetoothLeAdvertiser advertiser;
    private AdvertiseCallback advertising;
    private BluetoothDevice client;
    private String oldName, desiredName;
    private boolean naming;
    private int epoch, mtu = 23;
    private final ArrayDeque<BluetoothGattService> services = new ArrayDeque<>();
    private final Set<UUID> subscribed = new HashSet<>();
    private final ArrayDeque<byte[]> notifications = new ArrayDeque<>();
    private boolean notifying;
    private UUID advertisedUUID;
    private final BroadcastReceiver changes = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (BluetoothAdapter.ACTION_STATE_CHANGED.equals(intent.getAction()) &&
                    intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, -1) != BluetoothAdapter.STATE_ON) {
                stop(); activity.status("Bluetooth turned off. Enable Bluetooth, then Start.", false);
            } else if (naming && BluetoothAdapter.ACTION_LOCAL_NAME_CHANGED.equals(intent.getAction()) &&
                    desiredName.equals(intent.getStringExtra(BluetoothAdapter.EXTRA_LOCAL_NAME))) {
                naming = false; publishNext();
            }
        }
    };
    private boolean registered;
    Peripheral(MainActivity activity) { this.activity = activity; }
    static byte[] bytes(JSONArray a) throws JSONException {
        byte[] b = new byte[a.length()]; for(int i=0;i<b.length;i++) b[i]=(byte)a.getInt(i); return b;
    }
    void start(JSONObject profile) {
        stop(); final int generation = epoch;
        try {
            BluetoothManager manager = (BluetoothManager)activity.getSystemService(Context.BLUETOOTH_SERVICE);
            adapter = manager == null ? null : manager.getAdapter();
            if(adapter == null || !adapter.isEnabled()) throw new IllegalStateException("Bluetooth unavailable/off. Enable Bluetooth in Settings.");
            advertiser = adapter.getBluetoothLeAdvertiser();
            if(advertiser == null || !adapter.isMultipleAdvertisementSupported()) throw new IllegalStateException("This phone has no connectable BLE advertiser support.");
            desiredName = profile.getString("name"); advertisedUUID = UUID.fromString(profile.getString("advertised"));
            IntentFilter filter = new IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED);
            filter.addAction(BluetoothAdapter.ACTION_LOCAL_NAME_CHANGED);
            activity.registerReceiver(changes, filter); registered = true;
            server = manager.openGattServer(activity, callback(generation));
            if(server == null) throw new IllegalStateException("openGattServer failed");
            JSONArray specs = profile.getJSONArray("services");
            for(int i=0;i<specs.length();i++) {
                JSONObject spec=specs.getJSONObject(i);
                BluetoothGattService service=new BluetoothGattService(UUID.fromString(spec.getString("uuid")), BluetoothGattService.SERVICE_TYPE_PRIMARY);
                JSONArray chars=spec.getJSONArray("characteristics");
                for(int j=0;j<chars.length();j++) {
                    JSONObject cs=chars.getJSONObject(j); String flags=cs.getJSONArray("flags").toString();
                    int properties=0, permissions=0;
                    if(flags.contains("\"write\"")) properties|=BluetoothGattCharacteristic.PROPERTY_WRITE;
                    if(flags.contains("write-without-response")) properties|=BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE;
                    if(flags.contains("write")) permissions|=BluetoothGattCharacteristic.PERMISSION_WRITE;
                    if(flags.contains("read")) {properties|=BluetoothGattCharacteristic.PROPERTY_READ;permissions|=BluetoothGattCharacteristic.PERMISSION_READ;}
                    if(flags.contains("notify")) properties|=BluetoothGattCharacteristic.PROPERTY_NOTIFY;
                    BluetoothGattCharacteristic c=new BluetoothGattCharacteristic(UUID.fromString(cs.getString("uuid")),properties,permissions);
                    c.setValue(bytes(cs.getJSONArray("value")));
                    if(flags.contains("notify")) c.addDescriptor(new BluetoothGattDescriptor(CCCD,BluetoothGattDescriptor.PERMISSION_READ|BluetoothGattDescriptor.PERMISSION_WRITE));
                    service.addCharacteristic(c);
                }
                services.add(service);
            }
            oldName=adapter.getName();
            if(!desiredName.equals(oldName)) {
                naming=true;
                if(!adapter.setName(desiredName)) throw new IllegalStateException("Cannot set Bluetooth name");
                activity.status("Setting adapter name to " + desiredName, true);
                new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(() -> {
                    if(generation==epoch && naming) fail("Bluetooth name change timed out; retry Start");
                }, 5000);
            } else publishNext();
        } catch(Exception e) { fail(e.getMessage()); }
    }
    private void fail(String message) { stop(); activity.status("BLE error: " + message,false); }
    private void publishNext() {
        if(server==null) return;
        if(!services.isEmpty()) { if(!server.addService(services.remove())) fail("GATT service registration rejected"); return; }
        final int generation=epoch;
        advertising=new AdvertiseCallback() {
            @Override public void onStartSuccess(AdvertiseSettings settings) { activity.runOnUiThread(() -> {
                if(generation==epoch) activity.status("Advertising " + desiredName + " · one board / one controller",true);
            }); }
            @Override public void onStartFailure(int code) { activity.runOnUiThread(() -> {if(generation==epoch)fail("Advertising failed (code " + code + ")");}); }
        };
        // Full service UUID in advertisement; full board name in scan response.
        advertiser.startAdvertising(new AdvertiseSettings.Builder().setConnectable(true).setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY).build(),
            new AdvertiseData.Builder().addServiceUuid(new ParcelUuid(advertisedUUID)).build(),
            new AdvertiseData.Builder().setIncludeDeviceName(true).build(), advertising);
    }
    private BluetoothGattServerCallback callback(final int generation) {
        return new BluetoothGattServerCallback() {
            private void post(Runnable action) { activity.runOnUiThread(() -> {
                if(generation==epoch && server!=null) try {action.run();}catch(Exception e){fail(e.getMessage());}
            }); }
            @Override public void onServiceAdded(int status, BluetoothGattService service) {
                post(() -> {if(status==BluetoothGatt.GATT_SUCCESS)publishNext();else fail("GATT registration status "+status);});
            }
            @Override public void onConnectionStateChange(BluetoothDevice device,int status,int state) {
                post(() -> {
                    if(state==BluetoothProfile.STATE_CONNECTED) {
                        if(client!=null && !client.equals(device)) {server.cancelConnection(device);return;}
                        client=device;mtu=23;activity.status("Connected · " + device.getAddress(),true);
                    } else if(state==BluetoothProfile.STATE_DISCONNECTED && device.equals(client)) {
                        client=null;subscribed.clear();notifications.clear();notifying=false;activity.disconnected();
                        activity.status("Disconnected ("+status+") · advertising",true);
                    }
                });
            }
            @Override public void onMtuChanged(BluetoothDevice d,int value) {post(() -> {if(d.equals(client))mtu=value;});}
            @Override public void onCharacteristicWriteRequest(BluetoothDevice d,int request,BluetoothGattCharacteristic c,boolean prepared,boolean response,int offset,byte[] value) {
                byte[] copy=value.clone();
                post(() -> {
                    int result=prepared?BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED:offset!=0?BluetoothGatt.GATT_INVALID_OFFSET:
                        !d.equals(client)?BluetoothGatt.GATT_FAILURE:BluetoothGatt.GATT_SUCCESS;
                    if(response) server.sendResponse(d,request,result,offset,null);
                    if(result==BluetoothGatt.GATT_SUCCESS) activity.receive(copy, updates -> {
                        if(generation!=epoch || !d.equals(client))return;
                        try {
                            for(int i=0;i<updates.length();i++) {
                                JSONObject u=updates.getJSONObject(i);
                                if(u.has("state")) characteristic(STATE).setValue(bytes(u.getJSONArray("state")));
                                if(u.has("notify")&&!u.isNull("notify")&&subscribed.contains(UUID.fromString(NOTIFY))) {
                                    if(notifications.size()>=128) {fail("Notification queue overflow");return;}
                                    notifications.add(bytes(u.getJSONArray("notify")));
                                }
                            }
                            sendNotification();
                        } catch(Exception e) {fail("Reply: "+e.getMessage());}
                    });
                });
            }
            @Override public void onCharacteristicReadRequest(BluetoothDevice d,int request,int offset,BluetoothGattCharacteristic c) {
                post(() -> {
                    if(c.getUuid().toString().equals(STATE)) activity.readState(value -> {
                        if(generation==epoch && server!=null) readResponse(d,request,offset,value);
                    });
                    else readResponse(d,request,offset,c.getValue());
                });
            }
            @Override public void onDescriptorReadRequest(BluetoothDevice d,int request,int offset,BluetoothGattDescriptor desc) {
                post(() -> readResponse(d,request,offset,subscribed.contains(desc.getCharacteristic().getUuid())?BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE:BluetoothGattDescriptor.DISABLE_NOTIFICATION_VALUE));
            }
            @Override public void onDescriptorWriteRequest(BluetoothDevice d,int request,BluetoothGattDescriptor desc,boolean prepared,boolean response,int offset,byte[] value) {
                byte[] copy=value.clone();post(() -> {
                    int result=BluetoothGatt.GATT_SUCCESS;
                    if(prepared)result=BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED;
                    else if(offset!=0)result=BluetoothGatt.GATT_INVALID_OFFSET;
                    else if(!CCCD.equals(desc.getUuid()) || (!Arrays.equals(copy,new byte[]{0,0})&&!Arrays.equals(copy,new byte[]{1,0})))result=BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED;
                    else if(copy[0]==1)subscribed.add(desc.getCharacteristic().getUuid());
                    else subscribed.remove(desc.getCharacteristic().getUuid());
                    if(response)server.sendResponse(d,request,result,offset,null);
                });
            }
            @Override public void onExecuteWrite(BluetoothDevice d,int request,boolean execute) {post(() -> server.sendResponse(d,request,BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED,0,null));}
            @Override public void onNotificationSent(BluetoothDevice d,int status) {post(() -> {
                notifying=false;
                if(status!=BluetoothGatt.GATT_SUCCESS)fail("Notification failed "+status);else sendNotification();
            });}
        };
    }
    private BluetoothGattCharacteristic characteristic(String uuid) {
        for(BluetoothGattService s:server.getServices()) {BluetoothGattCharacteristic c=s.getCharacteristic(UUID.fromString(uuid));if(c!=null)return c;}
        throw new IllegalStateException("Missing characteristic "+uuid);
    }
    private void readResponse(BluetoothDevice d,int request,int offset,byte[] value) {
        if(offset<0||offset>value.length)server.sendResponse(d,request,BluetoothGatt.GATT_INVALID_OFFSET,offset,null);
        else server.sendResponse(d,request,BluetoothGatt.GATT_SUCCESS,offset,Arrays.copyOfRange(value,offset,Math.min(value.length,offset+mtu-1)));
    }
    private void sendNotification() {
        if(notifying||client==null||notifications.isEmpty())return;
        byte[] value=notifications.remove();
        // No invented application-level fragmentation: eWalls expects one broadcast.
        if(value.length>mtu-3) {activity.status("Quantum notification needs MTU >= "+(value.length+3)+"; fff4 read remains available",true);sendNotification();return;}
        BluetoothGattCharacteristic c=characteristic(NOTIFY);c.setValue(value);notifying=true;
        if(!server.notifyCharacteristicChanged(client,c,false))fail("Notification enqueue failed");
    }
    void stop() {
        epoch++;naming=false;
        try {
            if(advertiser!=null&&advertising!=null)advertiser.stopAdvertising(advertising);
            if(server!=null) {if(client!=null)server.cancelConnection(client);server.close();}
            if(adapter!=null&&adapter.isEnabled()&&oldName!=null&&desiredName!=null&&desiredName.equals(adapter.getName()))adapter.setName(oldName);
        } catch(SecurityException ignored) { /* Permissions can be revoked in Settings. */ }
        if(registered) {activity.unregisterReceiver(changes);registered=false;}
        server=null;client=null;advertising=null;oldName=null;desiredName=null;
        services.clear();subscribed.clear();notifications.clear();notifying=false;
    }
}
