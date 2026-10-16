// braw2ppm: decode one frame of a Blackmagic RAW clip as Film Gen 5 / Wide Gamut (the "Gen 5" lutlab
// reads), 16-bit, embedded look LUT off, written as a binary PPM. Derived from the ExtractFrame sample in
// the Blackmagic RAW SDK (Blackmagic Design, license in the sample). Build with build.sh. Checked against
// Resolve's own Gen 5 render of the same frame: median Delta E 0.3 once both are blurred (colour agrees;
// the difference is resampling).
#include "BlackmagicRawAPI.h"
#include <iostream>
#include <cstdio>
#include <string>
#include <CoreServices/CoreServices.h>

static const char* g_out = nullptr;
static int g_frame = 0;
static bool g_done = false;
static IBlackmagicRawClipProcessingAttributes* g_attrs = nullptr;

class CB : public IBlackmagicRawCallback {
public:
    virtual void ReadComplete(IBlackmagicRawJob* readJob, HRESULT result, IBlackmagicRawFrame* frame) {
        IBlackmagicRawJob* job = nullptr;
        if (result == S_OK) result = frame->SetResourceFormat(blackmagicRawResourceFormatRGBU16);
        if (result == S_OK) result = frame->CreateJobDecodeAndProcessFrame(g_attrs, nullptr, &job);
        if (result == S_OK) result = job->Submit();
        if (result != S_OK && job) job->Release();
        readJob->Release();
    }
    virtual void ProcessComplete(IBlackmagicRawJob* job, HRESULT result, IBlackmagicRawProcessedImage* img) {
        unsigned w = 0, h = 0, n = 0; void* data = nullptr;
        if (result == S_OK) result = img->GetWidth(&w);
        if (result == S_OK) result = img->GetHeight(&h);
        if (result == S_OK) result = img->GetResourceSizeBytes(&n);
        if (result == S_OK) result = img->GetResource(&data);
        if (result == S_OK) {
            FILE* f = fopen(g_out, "wb");
            if (!f) { std::cerr << "cannot write " << g_out << std::endl; job->Release(); return; }
            fprintf(f, "P6\n%u %u\n65535\n", w, h);
            const uint16_t* p = (const uint16_t*)data;
            for (size_t i = 0; i < (size_t)w * h * 3; i++) { uint16_t v = p[i]; fputc(v >> 8, f); fputc(v & 255, f); }
            fclose(f);
            std::cout << "wrote " << g_out << " " << w << "x" << h << " bytes " << n << std::endl;
            g_done = true;
        } else std::cerr << "process failed " << result << std::endl;
        job->Release();
    }
    virtual void DecodeComplete(IBlackmagicRawJob*, HRESULT) {}
    virtual void TrimProgress(IBlackmagicRawJob*, float) {}
    virtual void TrimComplete(IBlackmagicRawJob*, HRESULT) {}
    virtual void SidecarMetadataParseWarning(IBlackmagicRawClip*, CFStringRef, uint32_t, CFStringRef) {}
    virtual void SidecarMetadataParseError(IBlackmagicRawClip*, CFStringRef, uint32_t, CFStringRef) {}
    virtual void PreparePipelineComplete(void*, HRESULT) {}
    virtual HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, LPVOID*) { return E_NOTIMPL; }
    virtual ULONG STDMETHODCALLTYPE AddRef(void) { return 0; }
    virtual ULONG STDMETHODCALLTYPE Release(void) { return 0; }
};

int main(int argc, const char* argv[]) {
    if (argc < 4) { std::cerr << "usage: braw2ppm clip.braw (frame | SECONDSs) out.ppm\n"; return 1; }
    g_out = argv[3];
    const char* env = getenv("BRAW_SDK");   // the SDK's Mac folder, as for build.sh
    std::string libs = std::string(env ? env : "/Applications/Blackmagic RAW/Blackmagic RAW SDK/Mac") + "/Libraries";
    CFStringRef sdkLibraries = CFStringCreateWithCString(NULL, libs.c_str(), kCFStringEncodingUTF8);
    CFStringRef name = CFStringCreateWithCString(NULL, argv[1], kCFStringEncodingUTF8);
    IBlackmagicRawFactory* factory = CreateBlackmagicRawFactoryInstanceFromPath(sdkLibraries);
    if (!factory) { std::cerr << "no factory\n"; return 2; }
    IBlackmagicRaw* codec = nullptr; factory->CreateCodec(&codec);
    IBlackmagicRawClip* clip = nullptr;
    if (codec->OpenClip(name, &clip) != S_OK) { std::cerr << "open failed\n"; return 3; }
    IBlackmagicRawClipProcessingAttributes* attrs = nullptr;
    clip->CloneClipProcessingAttributes(&attrs); g_attrs = attrs;
    Variant v;
    const char* names[3] = {"gamma", "gamut", "lut"};
    for (int k = 0; k < 2; k++) {
        VariantInit(&v);
        BlackmagicRawClipProcessingAttribute a = k == 0 ? blackmagicRawClipProcessingAttributeGamma : blackmagicRawClipProcessingAttributeGamut;
        HRESULT r = attrs->GetClipAttribute(a, &v);
        if (r == S_OK && v.vt == blackmagicRawVariantTypeString) {
            char b[200]; CFStringGetCString(v.bstrVal, b, 200, kCFStringEncodingUTF8); std::cerr << names[k] << " default: " << b << std::endl;
        }
        VariantClear(&v);
    }
    VariantInit(&v); v.vt = blackmagicRawVariantTypeU16; v.uintVal = 5; attrs->SetClipAttribute(blackmagicRawClipProcessingAttributeColorScienceGen, &v);
    VariantInit(&v); v.vt = blackmagicRawVariantTypeString; v.bstrVal = CFSTR("Blackmagic Design Film"); std::cerr << "set gamma " << attrs->SetClipAttribute(blackmagicRawClipProcessingAttributeGamma, &v) << std::endl;
    VariantInit(&v); v.vt = blackmagicRawVariantTypeString; v.bstrVal = CFSTR("Blackmagic Design"); std::cerr << "set gamut " << attrs->SetClipAttribute(blackmagicRawClipProcessingAttributeGamut, &v) << std::endl;
    VariantInit(&v); v.vt = blackmagicRawVariantTypeString; v.bstrVal = CFSTR("Disabled"); std::cerr << "set lut " << attrs->SetClipAttribute(blackmagicRawClipProcessingAttributePost3DLUTMode, &v) << std::endl;
    // argv[2] is a frame index, or seconds with an "s" suffix (e.g. 3s, 2.5s)
    {
        std::string a = argv[2];
        if (!a.empty() && a.back() == 's') {
            float fps = 0; clip->GetFrameRate(&fps);
            g_frame = (int)(atof(a.c_str()) * fps + 0.5f);
        } else g_frame = atoi(a.c_str());
    }
    CB cb; codec->SetCallback(&cb);
    IBlackmagicRawJob* rj = nullptr;
    if (clip->CreateJobReadFrame(g_frame, &rj) != S_OK || rj->Submit() != S_OK) { std::cerr << "read failed\n"; return 4; }
    codec->FlushJobs();
    clip->Release(); codec->Release(); factory->Release();
    return g_done ? 0 : 5;
}
