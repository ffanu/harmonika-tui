"""Small complete native-info/channel source kits. No native code runs on this host."""
import copy,re
IDS=('bridge.kotlin.module','bridge.swift.module','bridge.react_native','bridge.flutter_platform_channel','bridge.method_channel','bridge.event_channel','bridge.type_safe')
KEYWORDS=frozenset('class object fun when interface package import public private protected static void int boolean switch case default new return this super null true false val var typealias'.split())
def schema(id):
 fw='swift' if id=='bridge.swift.module' else 'kotlin' if id=='bridge.kotlin.module' else 'flutter' if id in ('bridge.flutter_platform_channel','bridge.method_channel','bridge.event_channel') else 'react_native'
 return {'type':'object','additionalProperties':False,'properties':{'identifier':{'type':'string','pattern':'^[a-z][a-z0-9]*(?:\\.[a-z][a-z0-9]*){1,5}$','default':'id.harmonika.app'},'framework':{'type':'string','enum':[fw],'default':fw}}}
def descriptors():return {id:{'description':'Menghasilkan source kit native info atau bridge terikat tipe; integrasi/build native belum dilakukan.','input_schema':schema(id),'mode':'source_generation'} for id in IDS}
def dispatch(id,v):
 if id not in IDS or type(v) is not dict or set(v)-{'identifier','framework'}:raise ValueError('invalid_input')
 fw=schema(id)['properties']['framework']['default']
 if v.get('framework',fw)!=fw:raise ValueError('unsupported_framework')
 ident=v.get('identifier','id.harmonika.app')
 if type(ident) is not str or len(ident)>120 or not re.fullmatch(r'[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*){1,5}',ident) or any(p in KEYWORDS for p in ident.split('.')):raise ValueError('invalid_identifier')
 files={}
 if id=='bridge.kotlin.module':
  files['android/PlatformInfo.kt']='''package PACKAGE.bridge
import android.os.Build
/** Pure native info provider; no permissions or hardware access requested. */
object PlatformInfo {
    fun read(): Map<String, String> = mapOf("os" to "android", "version" to Build.VERSION.RELEASE)
}
'''.replace('PACKAGE',ident)
 elif id=='bridge.swift.module':
  files['ios/PlatformInfo.swift']='''import Foundation
public struct PlatformInfo {
    public let os = "ios"
    public let version: String
    public init() { version = ProcessInfo.processInfo.operatingSystemVersionString }
    public func dictionary() -> [String: String] { ["os": os, "version": version] }
}
'''
 elif id=='bridge.react_native':
  files['android/HarmonikaBridgeModule.kt']='''package PACKAGE.bridge
import android.os.Build
import com.facebook.react.bridge.*
class HarmonikaBridgeModule(context: ReactApplicationContext): ReactContextBaseJavaModule(context) {
    override fun getName() = "HarmonikaBridge"
    @ReactMethod fun getPlatformInfo(promise: Promise) {
        val result = Arguments.createMap()
        result.putString("os", "android")
        result.putString("version", Build.VERSION.RELEASE)
        promise.resolve(result)
    }
}
'''.replace('PACKAGE',ident)
  files['android/HarmonikaBridgePackage.kt']='''package PACKAGE.bridge
import com.facebook.react.ReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.uimanager.ViewManager
class HarmonikaBridgePackage: ReactPackage {
    override fun createNativeModules(context: ReactApplicationContext): List<NativeModule> = listOf(HarmonikaBridgeModule(context))
    override fun createViewManagers(context: ReactApplicationContext): List<ViewManager<*, *>> = emptyList()
}
'''.replace('PACKAGE',ident)
  files['ios/HarmonikaBridge.swift']='''import Foundation
@objc(HarmonikaBridge)
class HarmonikaBridge: NSObject {
    @objc static func requiresMainQueueSetup() -> Bool { false }
    @objc func getPlatformInfo(_ resolve: @escaping (Any?) -> Void, rejecter reject: @escaping (String?, String?, Error?) -> Void) {
        resolve(["os": "ios", "version": ProcessInfo.processInfo.operatingSystemVersionString])
    }
}
'''
  files['ios/HarmonikaBridgeExport.m']='''#import <React/RCTBridgeModule.h>
@interface RCT_EXTERN_MODULE(HarmonikaBridge, NSObject)
RCT_EXTERN_METHOD(getPlatformInfo:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
@end
'''
  files['src/nativeInfo.ts']=TYPESAFE
 elif id=='bridge.type_safe':files['src/nativeInfo.ts']=TYPESAFE
 elif id in ('bridge.flutter_platform_channel','bridge.method_channel'):
  files['lib/platform_info.dart']='''import 'package:flutter/services.dart';
class PlatformInfo {
  final String os;
  final String version;
  const PlatformInfo(this.os, this.version);
  static const _channel = MethodChannel('id.harmonika/platform');
  static Future<PlatformInfo> read() async {
    final value = await _channel.invokeMapMethod<String, Object?>('getPlatformInfo');
    final os = value?['os']; final version = value?['version'];
    if (os is! String || version is! String) throw const FormatException('Invalid native info response');
    return PlatformInfo(os, version);
  }
}
'''
  files['integration/android/MainActivity.kt']='''package PACKAGE
import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
class MainActivity: FlutterActivity() {
    override fun configureFlutterEngine(engine: FlutterEngine) {
        super.configureFlutterEngine(engine)
        MethodChannel(engine.dartExecutor.binaryMessenger, "id.harmonika/platform").setMethodCallHandler { call, result ->
            if (call.method == "getPlatformInfo") result.success(mapOf("os" to "android", "version" to Build.VERSION.RELEASE))
            else result.notImplemented()
        }
    }
}
'''.replace('PACKAGE',ident)
  files['integration/ios/PlatformChannel.swift']='''import Flutter
import Foundation
final class PlatformChannel {
    static func register(messenger: FlutterBinaryMessenger) {
        let channel = FlutterMethodChannel(name: "id.harmonika/platform", binaryMessenger: messenger)
        channel.setMethodCallHandler { call, result in
            guard call.method == "getPlatformInfo" else { result(FlutterMethodNotImplemented); return }
            result(["os": "ios", "version": ProcessInfo.processInfo.operatingSystemVersionString])
        }
    }
}
'''
 else:
  files['lib/native_ticks.dart']='''import 'package:flutter/services.dart';
/// Retain and cancel the listener subscription in the owning widget's dispose().
Stream<int> nativeTicks() => const EventChannel('id.harmonika/ticks')
    .receiveBroadcastStream().map((value) {
      if (value is! num) throw const FormatException('Invalid tick event');
      return value.toInt();
    });
'''
  files['integration/android/TickStream.kt']='''package PACKAGE.bridge
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import io.flutter.plugin.common.EventChannel
class TickStream: EventChannel.StreamHandler {
    private val handler = Handler(Looper.getMainLooper())
    private var sink: EventChannel.EventSink? = null
    private val task = object: Runnable {
        override fun run() {
            val listener = sink ?: return
            listener.success(SystemClock.elapsedRealtime())
            handler.postDelayed(this, 1000)
        }
    }
    override fun onListen(arguments: Any?, events: EventChannel.EventSink) {
        handler.removeCallbacks(task); sink = events; handler.post(task)
    }
    override fun onCancel(arguments: Any?) { sink = null; handler.removeCallbacks(task) }
}
'''.replace('PACKAGE',ident)
  files['integration/ios/TickStream.swift']='''import Flutter
import Foundation
final class TickStream: NSObject, FlutterStreamHandler {
    private var timer: Timer?
    func onListen(withArguments arguments: Any?, eventSink events: @escaping FlutterEventSink) -> FlutterError? {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            events(Int64(ProcessInfo.processInfo.systemUptime * 1000))
        }
        return nil
    }
    func onCancel(withArguments arguments: Any?) -> FlutterError? { timer?.invalidate(); timer = nil; return nil }
    deinit { timer?.invalidate() }
}
'''
 files['docs/native-integration.md']='''# Native source kit

Code has not been compiled, executed or registered. This is not an APK/IPA.
Copy selected modules into your existing project after reviewing the diff.
Flutter: register PlatformChannel.register(messenger:) with the engine messenger;
register EventChannel id.harmonika/ticks with TickStream on each platform, retain
handler, detach on engine teardown, and cancel Dart subscriptions in dispose.
Android MainActivity is an integration example: merge configureFlutterEngine,
do not replace a customized activity. Native module needs the platform SDK.
React Native: register HarmonikaBridgePackage in Android getPackages(), add Swift
and Objective-C exporter to the iOS target with React headers, and rebuild native
app. These are legacy-bridge source modules; validate compatibility/interop with
your RN architecture. A TurboModule/codegen implementation is not generated.
The type-safe TS wrapper rejects an unregistered bridge instead of inventing info.
No user/host hardware information was collected by this generator.
'''
 return {'status':'completed','mode':'source_generation','files':[{'path':p,'content':c} for p,c in files.items()],'warnings':['Native source kit only. Register in the existing project and test on emulator/device; no build or signing was performed.'],'runtime_verified':False}
TYPESAFE='''import { NativeModules } from 'react-native';
export type PlatformInfo = { os: string; version: string };
interface Bridge { getPlatformInfo(): Promise<unknown>; }
export async function platformInfo(): Promise<PlatformInfo> {
  const bridge = NativeModules.HarmonikaBridge as Bridge | undefined;
  if (!bridge || typeof bridge.getPlatformInfo !== 'function') throw new Error('Native bridge belum terdaftar.');
  const value = await bridge.getPlatformInfo();
  if (!value || typeof value !== 'object') throw new Error('Respons native tidak valid.');
  const data = value as Record<string, unknown>;
  if (typeof data.os !== 'string' || typeof data.version !== 'string') throw new Error('Respons native tidak valid.');
  return { os: data.os, version: data.version };
}
'''
