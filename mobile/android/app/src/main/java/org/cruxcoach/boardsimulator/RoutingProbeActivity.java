package org.cruxcoach.boardsimulator;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.bluetooth.*;
import android.bluetooth.le.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.os.*;
import android.widget.*;
import org.json.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Explicit, user-operated experiment. Two real advertisers and GATT servers.
 * Does NOT infer a board from callback timing, nor claim address isolation.
 * The log records which service instance receives writes to each advertised name.
 */
@SuppressLint("MissingPermission")
public final class RoutingProbeActivity extends Activity {
    private final BluetoothGattServer[] servers=new BluetoothGattServer[2];
    private final AdvertisingSetCallback[] callbacks=new AdvertisingSetCallback[2];
    private final JSONObject[] profiles=new JSONObject[2];
    private final Set<BluetoothDevice> peers=new HashSet<>();
    private final List<String> names=Arrays.asList("Kilter Board#p1@3","Tension Board#p2@3","QB_020000000001");
    private BluetoothAdapter adapter;
    private BluetoothLeAdvertiser advertiser;
    private TextView output;
    private String oldName, pendingName;
    private int pendingSlot, epoch;
    private boolean registered;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final StringBuilder log=new StringBuilder();
    private final BroadcastReceiver receiver=new BroadcastReceiver() {
        @Override public void onReceive(Context c,Intent intent) {
            if(pendingName!=null && pendingName.equals(intent.getStringExtra(BluetoothAdapter.EXTRA_LOCAL_NAME))) {
                pendingName=null; advertise(pendingSlot);
            }
        }
    };
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout panel=new LinearLayout(this);panel.setOrientation(LinearLayout.VERTICAL);
        TextView explanation=new TextView(this);
        explanation.setText("Routing experiment — not feature parity. Starting temporarily renames Bluetooth and publishes two real GATT servers/advertisers. Use a separate controller to scan both names and write; compare server/characteristic logs. No HCI-to-advertiser assignment is guessed.");
        panel.addView(explanation);
        button(panel,"Start duplicate UART (Kilter + Tension)",()->start(false));
        button(panel,"Start disjoint GATT (Kilter + Quantum)",()->start(true));
        button(panel,"Stop probe",this::stop);
        button(panel,"Copy probe log",()->((ClipboardManager)getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("Routing probe",log.toString())));
        ScrollView scroll=new ScrollView(this);output=new TextView(this);output.setTextIsSelectable(true);scroll.addView(output);
        panel.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));setContentView(panel);
        record("Probe idle; no Bluetooth changes until Start. API "+Build.VERSION.SDK_INT);
    }
    private void button(LinearLayout panel,String title,Runnable action) {
        Button button=new Button(this);button.setText(title);button.setOnClickListener(v->action.run());panel.addView(button);
    }
    private void record(String text) {
        log.append(new Date()).append(' ').append(text).append('\n');
        if(log.length()>64000)log.delete(0,log.length()-64000);
        output.setText(log.toString());
    }
    private void start(boolean disjoint) {
        stop();final int generation=epoch;
        try {
            if(Build.VERSION.SDK_INT>=31 && (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)!=PackageManager.PERMISSION_GRANTED || checkSelfPermission(Manifest.permission.BLUETOOTH_ADVERTISE)!=PackageManager.PERMISSION_GRANTED))throw new IllegalStateException("Grant Nearby devices in the main simulator first");
            BluetoothManager manager=(BluetoothManager)getSystemService(BLUETOOTH_SERVICE);
            adapter=manager.getAdapter();
            if(adapter==null||!adapter.isEnabled())throw new IllegalStateException("Bluetooth off/unavailable; enable it manually before this test");
            advertiser=adapter.getBluetoothLeAdvertiser();
            if(advertiser==null)throw new IllegalStateException("No advertiser");
            record("Capabilities: multiple="+adapter.isMultipleAdvertisementSupported()+", extended="+adapter.isLeExtendedAdvertisingSupported()+", maxData="+adapter.getLeMaximumAdvertisingDataLength());
            oldName=adapter.getName();
            java.io.ByteArrayOutputStream buffer=new java.io.ByteArrayOutputStream();
            try(java.io.InputStream input=getAssets().open("generated/catalog.json")) {
                byte[] chunk=new byte[8192];int count;
                while((count=input.read(chunk))!=-1)buffer.write(chunk,0,count);
            }
            JSONArray catalog=new JSONArray(new String(buffer.toByteArray(),StandardCharsets.UTF_8));
            for(int i=0;i<catalog.length();i++) {
                JSONObject p=catalog.getJSONObject(i);
                if(profiles[0]==null && p.getString("board").equals("kilter"))profiles[0]=p;
                if(profiles[1]==null && p.getString("board").equals(disjoint?"quantum":"tension"))profiles[1]=p;
            }
            profiles[0].put("name",names.get(0));profiles[1].put("name",names.get(disjoint?2:1));
            registerReceiver(receiver,new IntentFilter(BluetoothAdapter.ACTION_LOCAL_NAME_CHANGED));registered=true;
            for(int slot=0;slot<2;slot++) {
                final int index=slot;
                servers[slot]=manager.openGattServer(this,new BluetoothGattServerCallback() {
                    private void post(Runnable work) {runOnUiThread(()->{if(generation==epoch)try{work.run();}catch(Exception e){record("Error "+e);stop();}});}
                    @Override public void onServiceAdded(int status,BluetoothGattService service) {
                        post(()->{
                            record("server="+index+" service="+service.getUuid()+" instance="+service.getInstanceId()+" status="+status);
                            if(status!=0){stop();return;}
                            // Register one service at a time, advertise only after the last callback.
                            publishNext(index);
                        });
                    }
                    @Override public void onConnectionStateChange(BluetoothDevice device,int status,int state) {
                        post(()->{if(state==BluetoothProfile.STATE_CONNECTED)peers.add(device);else peers.remove(device);
                            record("server="+index+" peer="+device.getAddress()+" state="+state+" status="+status+" (no advertiser ID in callback)");});
                    }
                    @Override public void onCharacteristicWriteRequest(BluetoothDevice device,int request,BluetoothGattCharacteristic c,boolean prepared,boolean response,int offset,byte[] value) {
                        post(()->{
                            record("WRITE server="+index+" peer="+device.getAddress()+" uuid="+c.getUuid()+" instance="+c.getInstanceId()+" bytes="+hex(value));
                            if(response)servers[index].sendResponse(device,request,prepared?BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED:offset!=0?BluetoothGatt.GATT_INVALID_OFFSET:BluetoothGatt.GATT_SUCCESS,offset,null);
                        });
                    }
                    @Override public void onCharacteristicReadRequest(BluetoothDevice device,int request,int offset,BluetoothGattCharacteristic c) {
                        post(()->{
                            record("READ server="+index+" peer="+device.getAddress()+" uuid="+c.getUuid());
                            byte[] value=c.getValue();
                            servers[index].sendResponse(device,request,offset>value.length?BluetoothGatt.GATT_INVALID_OFFSET:0,offset,offset>value.length?null:Arrays.copyOfRange(value,offset,value.length));
                        });
                    }
                });
                if(servers[slot]==null)throw new IllegalStateException("GATT server "+slot+" unavailable");
                publishNext(slot);
            }
        } catch(Exception e) {record("Probe failed: "+e.getMessage());stop();}
    }
    private final int[] nextService={0,0};
    private final boolean[] ready={false,false};
    private void publishNext(int slot) {
        try {
            JSONArray specs=profiles[slot].getJSONArray("services");
            if(nextService[slot]>=specs.length()) {
                ready[slot]=true;if(ready[0]&&ready[1])rename(0);return;
            }
            JSONObject spec=specs.getJSONObject(nextService[slot]++);
            BluetoothGattService service=new BluetoothGattService(UUID.fromString(spec.getString("uuid")),BluetoothGattService.SERVICE_TYPE_PRIMARY);
            JSONArray chars=spec.getJSONArray("characteristics");
            for(int i=0;i<chars.length();i++) {
                JSONObject entry=chars.getJSONObject(i);String flags=entry.getJSONArray("flags").toString();
                int properties=0,permissions=0;
                if(flags.contains("write")){properties|=BluetoothGattCharacteristic.PROPERTY_WRITE|BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE;permissions|=BluetoothGattCharacteristic.PERMISSION_WRITE;}
                if(flags.contains("read")){properties|=BluetoothGattCharacteristic.PROPERTY_READ;permissions|=BluetoothGattCharacteristic.PERMISSION_READ;}
                if(flags.contains("notify"))properties|=BluetoothGattCharacteristic.PROPERTY_NOTIFY;
                BluetoothGattCharacteristic c=new BluetoothGattCharacteristic(UUID.fromString(entry.getString("uuid")),properties,permissions);
                c.setValue(Peripheral.bytes(entry.getJSONArray("value")));service.addCharacteristic(c);
            }
            if(!servers[slot].addService(service))throw new IllegalStateException("Service add rejected");
        }catch(Exception e){record("Registration error "+e);stop();}
    }
    private void rename(int slot) throws JSONException {
        pendingSlot=slot;pendingName=profiles[slot].getString("name");
        if(pendingName.equals(adapter.getName())) {pendingName=null;advertise(slot);return;}
        if(!adapter.setName(pendingName)) {record("Rename rejected");stop();return;}
        final int generation=epoch;
        handler.postDelayed(()->{if(generation==epoch&&pendingName!=null){record("Rename timeout");stop();}},5000);
    }
    private void advertise(int slot) {
        final int generation=epoch;
        callbacks[slot]=new AdvertisingSetCallback() {
            @Override public void onAdvertisingSetStarted(AdvertisingSet set,int txPower,int status) {
                if(generation!=epoch)return;
                record("advertiser="+slot+" start status="+status+" tx="+txPower);
                if(status!=0){stop();return;}
                try {if(slot==0)rename(1);else record("Both advertiser callbacks succeeded. Independent address/GATT routing NOT yet proven. Connect separately from another device.");}
                catch(Exception e){record("Error "+e);stop();}
            }
            @Override public void onAdvertisingEnabled(AdvertisingSet set,boolean enabled,int status) {
                if(generation==epoch)record("advertiser="+slot+" enabled="+enabled+" status="+status+" (no peer/connection handle)");
            }
            @Override public void onAdvertisingSetStopped(AdvertisingSet set) {if(generation==epoch)record("advertiser="+slot+" stopped");}
        };
        try {
            advertiser.startAdvertisingSet(new AdvertisingSetParameters.Builder().setLegacyMode(true).setConnectable(true).setScannable(true).build(),
                new AdvertiseData.Builder().addServiceUuid(new ParcelUuid(UUID.fromString(profiles[slot].getString("advertised")))).build(),
                new AdvertiseData.Builder().setIncludeDeviceName(true).build(),null,null,callbacks[slot],handler);
        }catch(Exception e){record("Advertising error "+e);stop();}
    }
    private static String hex(byte[] bytes) {StringBuilder s=new StringBuilder();for(byte b:bytes)s.append(String.format(Locale.ROOT,"%02x",b&255));return s.toString();}
    private void stop() {
        epoch++;pendingName=null;
        try {
            for(int i=0;i<2;i++) {
                if(advertiser!=null&&callbacks[i]!=null)advertiser.stopAdvertisingSet(callbacks[i]);
                if(servers[i]!=null){for(BluetoothDevice peer:peers)servers[i].cancelConnection(peer);servers[i].close();}
                servers[i]=null;callbacks[i]=null;profiles[i]=null;nextService[i]=0;ready[i]=false;
            }
            if(oldName!=null&&adapter!=null&&adapter.isEnabled()&&names.contains(adapter.getName()))adapter.setName(oldName);
        }catch(SecurityException e){record("Permission revoked during cleanup");}
        oldName=null;peers.clear();
        if(registered){unregisterReceiver(receiver);registered=false;}
    }
    @Override public void onStop(){stop();super.onStop();}
}
