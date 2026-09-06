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
    private final int slot;
    private boolean multiplexed;
    private BluetoothAdapter adapter;
    private BluetoothGattServer server;
    private BluetoothLeAdvertiser advertiser;
    private AdvertisingSetCallback advertising;
    private AdvertisingSet advertisingSet;
    private boolean advertisementEnabled;
    private static final class Link {
        final BluetoothDevice device;
        int mtu=23; boolean assigned;
        final Set<UUID> subscribed=new HashSet<>();
        Link(BluetoothDevice device) { this.device=device; }
    }
    private static final class Notification {
        final Link link; final byte[] value;
        Notification(Link link, byte[] value) {this.link=link;this.value=value;}
    }
    private final Map<BluetoothDevice,Link> links=new LinkedHashMap<>();
    private boolean multi, servicesReady;
    private int advertisingEpoch;
    private String oldName, desiredName;
    private boolean naming;
    private int epoch,runToken;
    private final ArrayDeque<BluetoothGattService> services = new ArrayDeque<>();
    private final ArrayDeque<Notification> notifications = new ArrayDeque<>();
    private boolean notifying;
    private Notification inFlight;
    private UUID advertisedUUID;
    private final BroadcastReceiver changes = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (BluetoothAdapter.ACTION_STATE_CHANGED.equals(intent.getAction()) &&
                    intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, -1) != BluetoothAdapter.STATE_ON) {
                stop(); status("Bluetooth turned off. Enable Bluetooth, then Start.", false);
            } else if (naming && BluetoothAdapter.ACTION_LOCAL_NAME_CHANGED.equals(intent.getAction()) &&
                    desiredName.equals(intent.getStringExtra(BluetoothAdapter.EXTRA_LOCAL_NAME))) {
                naming = false; publishNext();
            }
        }
    };
    private boolean registered;
    Peripheral(MainActivity activity,int slot) { this.activity = activity;this.slot=slot; }
    void setMultiplexed(boolean enabled) { multiplexed=enabled; }
    private void status(String message,boolean running) {activity.status(slot,message,running);}
    private long assignedCount(){return links.values().stream().filter(l->l.assigned).count();}
    private boolean assign(BluetoothDevice device) {
        Link link=links.get(device);
        if(link==null){link=new Link(device);links.put(device,link);}
        if(!link.assigned && !multi && assignedCount()>0)return false;
        link.assigned=true;syncAdvertising();return true;
    }
    static byte[] bytes(JSONArray a) throws JSONException {
        byte[] b = new byte[a.length()]; for(int i=0;i<b.length;i++) b[i]=(byte)a.getInt(i); return b;
    }
    void start(JSONObject profile, boolean multi) {
        stop(); this.multi=multi;runToken=profile.optInt("runToken"); final int generation = epoch;
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
                status("Setting adapter name to " + desiredName, true);
                new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(() -> {
                    if(generation==epoch && naming) fail("Bluetooth name change timed out; retry Start");
                }, 5000);
            } else publishNext();
        } catch(Exception e) { fail(e.getMessage()); }
    }
    private void fail(String message) { stop(); status("BLE error: " + message,false); }
    private void publishNext() {
        if(server==null) return;
        if(!services.isEmpty()) { if(!server.addService(services.remove())) fail("GATT service registration rejected"); return; }
        servicesReady=true;
        syncAdvertising();
    }
    void setMulti(boolean enabled) {
        multi=enabled;
        if(server!=null && servicesReady)syncAdvertising();
    }
    private void stopAdvertisingOnly() {
        if(advertisingSet!=null && advertisementEnabled) {
            advertisementEnabled=false;advertisingSet.enableAdvertising(false,0,0);
        }
    }
    private void syncAdvertising() {
        if(!servicesReady)return;
        boolean wanted=multi || assignedCount()==0;
        if(advertisingSet!=null) {
            if(wanted!=advertisementEnabled) {
                advertisementEnabled=wanted;advertisingSet.enableAdvertising(wanted,0,0);
            }
            if(!wanted)status("Exclusive: "+assignedCount()+" controller(s) · advertising stopped"+(multiplexed?" after GATT access; address assignment unavailable":""),true);
            return;
        }
        if(advertising!=null)return;
        final int generation=epoch, advGeneration=++advertisingEpoch;
        advertising=new AdvertisingSetCallback() {
            @Override public void onAdvertisingSetStarted(AdvertisingSet set,int txPower,int result) {
                if(generation!=epoch || advGeneration!=advertisingEpoch)return;
                if(result!=ADVERTISE_SUCCESS){fail("Advertising failed (code "+result+")");return;}
                advertisingSet=set;advertisementEnabled=true;
                status("Advertising "+desiredName+(multi?" · multi-connect":" · exclusive"),true);
                syncAdvertising();activity.ready(slot);
            }
            @Override public void onAdvertisingEnabled(AdvertisingSet set,boolean enabled,int result) {
                if(generation!=epoch || advGeneration!=advertisingEpoch)return;
                if(result!=ADVERTISE_SUCCESS){fail("Advertising mode update failed "+result);return;}
                // A callback can describe an older queued enable request; current
                // desired state remains authoritative and was queued in order.
                status((enabled?"Advertising ":"Advertising stopped: ")+desiredName+" · "+assignedCount()+" controller(s)",true);
            }
        };
        // Create each set once while its adapter name is selected. Exclusive
        // pause/resume toggles the existing set, preserving that name snapshot
        // when another board later changes the adapter's global local name.
        advertiser.startAdvertisingSet(new AdvertisingSetParameters.Builder().setLegacyMode(true).setConnectable(true).setScannable(true).setInterval(AdvertisingSetParameters.INTERVAL_LOW).build(),
            new AdvertiseData.Builder().addServiceUuid(new ParcelUuid(advertisedUUID)).build(),
            new AdvertiseData.Builder().setIncludeDeviceName(true).build(),null,null,advertising,
            new android.os.Handler(android.os.Looper.getMainLooper()));
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
                        if(!multiplexed && !multi && !links.isEmpty() && !links.containsKey(device)) {server.cancelConnection(device);return;}
                        if(!links.containsKey(device))links.put(device,new Link(device));
                        if(!multiplexed)links.get(device).assigned=true;
                        status("Connected · " + device.getAddress()+" · "+links.size()+" controller(s)",true);
                        syncAdvertising();
                    } else if(state==BluetoothProfile.STATE_DISCONNECTED && links.containsKey(device)) {
                        Link old=links.remove(device);
                        notifications.removeIf(n->n.link==old);
                        if(inFlight!=null && inFlight.link==old){inFlight=null;notifying=false;sendNotification();}
                        activity.disconnected(slot,device.getAddress());
                        status("Disconnected ("+status+") · "+links.size()+" controller(s)",true);
                        syncAdvertising();
                    }
                });
            }
            @Override public void onMtuChanged(BluetoothDevice d,int value) {post(() -> {if(links.containsKey(d))links.get(d).mtu=value;});}
            @Override public void onCharacteristicWriteRequest(BluetoothDevice d,int request,BluetoothGattCharacteristic c,boolean prepared,boolean response,int offset,byte[] value) {
                byte[] copy=value.clone();
                post(() -> {
                    int result=prepared?BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED:offset!=0?BluetoothGatt.GATT_INVALID_OFFSET:
                        !assign(d)?BluetoothGatt.GATT_FAILURE:BluetoothGatt.GATT_SUCCESS;
                    if(response) server.sendResponse(d,request,result,offset,null);
                    Link link=links.get(d);
                    if(result==BluetoothGatt.GATT_SUCCESS) activity.receive(slot,runToken,d.getAddress(),copy, updates -> {
                        if(generation!=epoch || links.get(d)!=link)return;
                        try {
                            for(int i=0;i<updates.length();i++) {
                                JSONObject u=updates.getJSONObject(i);
                                if(u.has("state")) characteristic(STATE).setValue(bytes(u.getJSONArray("state")));
                                if(u.has("notify")&&!u.isNull("notify")) {
                                    byte[] notification=bytes(u.getJSONArray("notify"));
                                    boolean exception=notification.length>1 && (notification[1]&128)!=0;
                                    for(Link target:links.values())if(target.subscribed.contains(UUID.fromString(NOTIFY)) && (!exception || target==link)) {
                                        if(notifications.size()>=128) {fail("Notification queue overflow");return;}
                                        notifications.add(new Notification(target,notification));
                                    }
                                }
                            }
                            sendNotification();
                        } catch(Exception e) {fail("Reply: "+e.getMessage());}
                    });
                });
            }
            @Override public void onCharacteristicReadRequest(BluetoothDevice d,int request,int offset,BluetoothGattCharacteristic c) {
                post(() -> {
                    if(!assign(d)){server.sendResponse(d,request,BluetoothGatt.GATT_FAILURE,offset,null);return;}
                    if(c.getUuid().toString().equals(STATE)) activity.readState(slot,value -> {
                        if(generation==epoch && server!=null) readResponse(d,request,offset,value);
                    });
                    else readResponse(d,request,offset,c.getValue());
                });
            }
            @Override public void onDescriptorReadRequest(BluetoothDevice d,int request,int offset,BluetoothGattDescriptor desc) {
                post(() -> readResponse(d,request,offset,links.containsKey(d)&&links.get(d).subscribed.contains(desc.getCharacteristic().getUuid())?BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE:BluetoothGattDescriptor.DISABLE_NOTIFICATION_VALUE));
            }
            @Override public void onDescriptorWriteRequest(BluetoothDevice d,int request,BluetoothGattDescriptor desc,boolean prepared,boolean response,int offset,byte[] value) {
                byte[] copy=value.clone();post(() -> {
                    int result=BluetoothGatt.GATT_SUCCESS;
                    if(prepared)result=BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED;
                    else if(offset!=0)result=BluetoothGatt.GATT_INVALID_OFFSET;
                    else if(!CCCD.equals(desc.getUuid()) || (!Arrays.equals(copy,new byte[]{0,0})&&!Arrays.equals(copy,new byte[]{1,0})))result=BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED;
                    else if(!assign(d))result=BluetoothGatt.GATT_FAILURE;
                    else if(copy[0]==1)links.get(d).subscribed.add(desc.getCharacteristic().getUuid());
                    else links.get(d).subscribed.remove(desc.getCharacteristic().getUuid());
                    if(response)server.sendResponse(d,request,result,offset,null);
                });
            }
            @Override public void onExecuteWrite(BluetoothDevice d,int request,boolean execute) {post(() -> server.sendResponse(d,request,BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED,0,null));}
            @Override public void onNotificationSent(BluetoothDevice d,int status) {post(() -> {
                if(inFlight==null || !inFlight.link.device.equals(d))return;
                inFlight=null;notifying=false;
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
        else server.sendResponse(d,request,BluetoothGatt.GATT_SUCCESS,offset,Arrays.copyOfRange(value,offset,Math.min(value.length,offset+(links.containsKey(d)?links.get(d).mtu:23)-1)));
    }
    private void sendNotification() {
        if(notifying)return;
        while(!notifications.isEmpty()) {
            Notification n=notifications.remove();
            if(links.get(n.link.device)!=n.link)continue;
            if(n.value.length>n.link.mtu-3) {
                status("Quantum notification for "+n.link.device.getAddress()+" needs MTU >= "+(n.value.length+3)+"; fff4 is readable",true);
                continue;
            }
            BluetoothGattCharacteristic c=characteristic(NOTIFY);c.setValue(n.value);notifying=true;inFlight=n;
            if(!server.notifyCharacteristicChanged(n.link.device,c,false))fail("Notification enqueue failed");
            return;
        }
    }
    void stop() {
        epoch++;naming=false;servicesReady=false;
        try {
            advertisingEpoch++;
            if(advertiser!=null&&advertising!=null)advertiser.stopAdvertisingSet(advertising);
            if(server!=null) {for(Link link:links.values())if(link.assigned)server.cancelConnection(link.device);server.close();}
            if(!multiplexed&&adapter!=null&&adapter.isEnabled()&&oldName!=null&&desiredName!=null&&desiredName.equals(adapter.getName()))adapter.setName(oldName);
        } catch(SecurityException ignored) { /* Permissions can be revoked in Settings. */ }
        if(registered) {activity.unregisterReceiver(changes);registered=false;}
        server=null;links.clear();advertising=null;advertisingSet=null;advertisementEnabled=false;oldName=null;desiredName=null;
        services.clear();notifications.clear();notifying=false;inFlight=null;
    }
}
