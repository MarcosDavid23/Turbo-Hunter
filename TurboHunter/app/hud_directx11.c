/*
 * TurboHunter HUD DirectX 11 - 0.4.1
 *
 * Compilado dentro do processo pelo Frida CModule. Não cria janela externa.
 * Desenha "AGUARDANDO SOLO" imediatamente, troca para "ABATES: N" após a
 * confirmação segura e mostra temporariamente "RECOLHA OS ANIMAIS" ao atingir
 * 20 cadáveres. Restaura o estado gráfico do jogo através de um command list.
 */

#include <stddef.h>
#include <stdint.h>

#ifdef HUD_OFFLINE_TEST
typedef struct _GumInvocationContext GumInvocationContext;
extern void *gum_invocation_context_get_nth_argument(
    GumInvocationContext *context,
    unsigned int index
);
#else
#include <gum/guminterceptor.h>
#endif

typedef int32_t HRESULT;
typedef uint32_t UINT;
typedef int32_t BOOL;

typedef struct Guid {
    uint32_t data1;
    uint16_t data2;
    uint16_t data3;
    uint8_t data4[8];
} Guid;

typedef struct DxgiRational {
    UINT numerator;
    UINT denominator;
} DxgiRational;

typedef struct DxgiModeDesc {
    UINT width;
    UINT height;
    DxgiRational refresh_rate;
    UINT format;
    UINT scanline_ordering;
    UINT scaling;
} DxgiModeDesc;

typedef struct DxgiSampleDesc {
    UINT count;
    UINT quality;
} DxgiSampleDesc;

typedef struct DxgiSwapChainDesc {
    DxgiModeDesc buffer_desc;
    DxgiSampleDesc sample_desc;
    UINT buffer_usage;
    UINT buffer_count;
    void *output_window;
    BOOL windowed;
    UINT swap_effect;
    UINT flags;
} DxgiSwapChainDesc;

typedef struct D3d11BufferDesc {
    UINT byte_width;
    UINT usage;
    UINT bind_flags;
    UINT cpu_access_flags;
    UINT misc_flags;
    UINT structure_byte_stride;
} D3d11BufferDesc;

typedef struct D3d11SubresourceData {
    const void *system_memory;
    UINT system_memory_pitch;
    UINT system_memory_slice_pitch;
} D3d11SubresourceData;

typedef struct D3d11InputElementDesc {
    const char *semantic_name;
    UINT semantic_index;
    UINT format;
    UINT input_slot;
    UINT aligned_byte_offset;
    UINT input_slot_class;
    UINT instance_data_step_rate;
} D3d11InputElementDesc;

typedef struct D3d11RenderTargetBlendDesc {
    BOOL blend_enable;
    UINT source_blend;
    UINT destination_blend;
    UINT blend_operation;
    UINT source_blend_alpha;
    UINT destination_blend_alpha;
    UINT blend_operation_alpha;
    uint8_t render_target_write_mask;
    uint8_t padding[3];
} D3d11RenderTargetBlendDesc;

typedef struct D3d11BlendDesc {
    BOOL alpha_to_coverage_enable;
    BOOL independent_blend_enable;
    D3d11RenderTargetBlendDesc render_target[8];
} D3d11BlendDesc;

typedef struct D3d11DepthStencilOpDesc {
    UINT stencil_fail_operation;
    UINT stencil_depth_fail_operation;
    UINT stencil_pass_operation;
    UINT stencil_function;
} D3d11DepthStencilOpDesc;

typedef struct D3d11DepthStencilDesc {
    BOOL depth_enable;
    UINT depth_write_mask;
    UINT depth_function;
    BOOL stencil_enable;
    uint8_t stencil_read_mask;
    uint8_t stencil_write_mask;
    uint8_t padding[2];
    D3d11DepthStencilOpDesc front_face;
    D3d11DepthStencilOpDesc back_face;
} D3d11DepthStencilDesc;

typedef struct D3d11RasterizerDesc {
    UINT fill_mode;
    UINT cull_mode;
    BOOL front_counter_clockwise;
    int32_t depth_bias;
    float depth_bias_clamp;
    float slope_scaled_depth_bias;
    BOOL depth_clip_enable;
    BOOL scissor_enable;
    BOOL multisample_enable;
    BOOL antialiased_line_enable;
} D3d11RasterizerDesc;

typedef struct D3d11Viewport {
    float top_left_x;
    float top_left_y;
    float width;
    float height;
    float minimum_depth;
    float maximum_depth;
} D3d11Viewport;

typedef struct HudVertex {
    float x;
    float y;
    float red;
    float green;
    float blue;
    float alpha;
} HudVertex;

extern void *CreateWindowExW(
    uint32_t extended_style,
    const uint16_t *class_name,
    const uint16_t *window_name,
    uint32_t style,
    int x,
    int y,
    int width,
    int height,
    void *parent,
    void *menu,
    void *instance,
    void *parameter
);

extern int DestroyWindow(void *window);

extern HRESULT D3D11CreateDeviceAndSwapChain(
    void *adapter,
    UINT driver_type,
    void *software,
    UINT flags,
    const UINT *feature_levels,
    UINT feature_level_count,
    UINT sdk_version,
    const DxgiSwapChainDesc *swap_chain_desc,
    void **swap_chain,
    void **device,
    UINT *feature_level,
    void **immediate_context
);

extern HRESULT D3DCompile(
    const void *source_data,
    size_t source_size,
    const char *source_name,
    const void *defines,
    void *include_handler,
    const char *entry_point,
    const char *target,
    UINT flags1,
    UINT flags2,
    void **code_blob,
    void **error_blob
);

#define HUD_MAX_VERTICES 8192

static const Guid IID_ID3D11_DEVICE = {
    0xdb6f6ddbu, 0xac77u, 0x4e88u,
    {0x82u, 0x53u, 0x81u, 0x9du, 0xf9u, 0xbbu, 0xf1u, 0x40u}
};

static const Guid IID_ID3D11_TEXTURE2D = {
    0x6f15aaf2u, 0xd208u, 0x4e89u,
    {0x9au, 0xb4u, 0x48u, 0x95u, 0x35u, 0xd3u, 0x4fu, 0x9cu}
};

static const char HUD_VERTEX_SHADER[] =
    "struct VSInput{float2 position:POSITION;float4 color:COLOR0;};"
    "struct VSOutput{float4 position:SV_POSITION;float4 color:COLOR0;};"
    "VSOutput VSMain(VSInput input){VSOutput output;"
    "output.position=float4(input.position,0.0,1.0);"
    "output.color=input.color;return output;}";

static const char HUD_PIXEL_SHADER[] =
    "struct PSInput{float4 position:SV_POSITION;float4 color:COLOR0;};"
    "float4 PSMain(PSInput input):SV_TARGET{return input.color;}";

/*
 * O CModule do Frida mantém os próprios dados como somente leitura. Todo o
 * estado mutável fica, portanto, neste bloco externo alocado pelo JavaScript.
 * Isso também mantém o callback de Present inteiramente nativo.
 */
typedef struct HudState {
    volatile int enabled;
    volatile int pending_count;
    volatile int corner;
    volatile int warning;
    volatile int solo_ready;
    volatile int dirty;
    volatile int status;
    volatile int in_render;

    void *present_address;
    void *resize_buffers_address;
    void *resize_target_address;
    void *swap_chain;
    void *failed_swap_chain;
    void *device;
    void *immediate_context;
    void *deferred_context;
    void *render_target;
    void *vertex_shader;
    void *pixel_shader;
    void *input_layout;
    void *blend_state;
    void *depth_state;
    void *rasterizer_state;
    void *vertex_buffer;
    void *command_list;

    UINT width;
    UINT height;
    UINT vertex_count;
    HudVertex vertices[HUD_MAX_VERTICES];
} HudState;

#ifdef HUD_OFFLINE_TEST
static HudState hud_state;
#else
extern HudState hud_state;
#endif

#define g_enabled                (hud_state.enabled)
#define g_pending_count          (hud_state.pending_count)
#define g_corner                 (hud_state.corner)
#define g_warning                (hud_state.warning)
#define g_solo_ready             (hud_state.solo_ready)
#define g_dirty                  (hud_state.dirty)
#define g_status                 (hud_state.status)
#define g_in_render              (hud_state.in_render)
#define g_present_address        (hud_state.present_address)
#define g_resize_buffers_address (hud_state.resize_buffers_address)
#define g_resize_target_address  (hud_state.resize_target_address)
#define g_swap_chain             (hud_state.swap_chain)
#define g_failed_swap_chain      (hud_state.failed_swap_chain)
#define g_device                 (hud_state.device)
#define g_immediate_context      (hud_state.immediate_context)
#define g_deferred_context       (hud_state.deferred_context)
#define g_render_target          (hud_state.render_target)
#define g_vertex_shader          (hud_state.vertex_shader)
#define g_pixel_shader           (hud_state.pixel_shader)
#define g_input_layout           (hud_state.input_layout)
#define g_blend_state            (hud_state.blend_state)
#define g_depth_state            (hud_state.depth_state)
#define g_rasterizer_state       (hud_state.rasterizer_state)
#define g_vertex_buffer          (hud_state.vertex_buffer)
#define g_command_list           (hud_state.command_list)
#define g_width                  (hud_state.width)
#define g_height                 (hud_state.height)
#define g_vertex_count           (hud_state.vertex_count)
#define g_vertices               (hud_state.vertices)

static void zero_memory(void *memory, size_t size) {
    uint8_t *bytes = (uint8_t *) memory;
    size_t index;
    for (index = 0; index < size; index++)
        bytes[index] = 0;
}

static size_t text_length(const char *text) {
    size_t length = 0;
    while (text[length] != '\0')
        length++;
    return length;
}

static void **vtable(void *object) {
    return object ? *((void ***) object) : 0;
}

static void release_com(void **object) {
    typedef UINT (*ReleaseFunction)(void *self);
    void **table;

    if (!object || !*object)
        return;

    table = vtable(*object);
    if (table)
        ((ReleaseFunction) table[2])(*object);
    *object = 0;
}

static int succeeded(HRESULT result) {
    return result >= 0;
}

static const uint8_t *glyph_rows(char character) {
    static const uint8_t blank[7] = {0, 0, 0, 0, 0, 0, 0};
    static const uint8_t a[7] = {0x0e, 0x11, 0x11, 0x1f, 0x11, 0x11, 0x11};
    static const uint8_t b[7] = {0x1e, 0x11, 0x11, 0x1e, 0x11, 0x11, 0x1e};
    static const uint8_t c[7] = {0x0f, 0x10, 0x10, 0x10, 0x10, 0x10, 0x0f};
    static const uint8_t d[7] = {0x1e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1e};
    static const uint8_t t[7] = {0x1f, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04};
    static const uint8_t e[7] = {0x1f, 0x10, 0x10, 0x1e, 0x10, 0x10, 0x1f};
    static const uint8_t g[7] = {0x0e, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0f};
    static const uint8_t h[7] = {0x11, 0x11, 0x11, 0x1f, 0x11, 0x11, 0x11};
    static const uint8_t i[7] = {0x0e, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0e};
    static const uint8_t k[7] = {0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11};
    static const uint8_t l[7] = {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1f};
    static const uint8_t m[7] = {0x11, 0x1b, 0x15, 0x15, 0x11, 0x11, 0x11};
    static const uint8_t n[7] = {0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11};
    static const uint8_t o[7] = {0x0e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e};
    static const uint8_t r[7] = {0x1e, 0x11, 0x11, 0x1e, 0x14, 0x12, 0x11};
    static const uint8_t s[7] = {0x0f, 0x10, 0x10, 0x0e, 0x01, 0x01, 0x1e};
    static const uint8_t u[7] = {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e};
    static const uint8_t w[7] = {0x11, 0x11, 0x11, 0x15, 0x15, 0x1b, 0x11};
    static const uint8_t colon[7] = {0x00, 0x04, 0x04, 0x00, 0x04, 0x04, 0x00};
    static const uint8_t zero[7] = {0x0e, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0e};
    static const uint8_t one[7] = {0x04, 0x0c, 0x04, 0x04, 0x04, 0x04, 0x0e};
    static const uint8_t two[7] = {0x0e, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1f};
    static const uint8_t three[7] = {0x1e, 0x01, 0x01, 0x0e, 0x01, 0x01, 0x1e};
    static const uint8_t four[7] = {0x02, 0x06, 0x0a, 0x12, 0x1f, 0x02, 0x02};
    static const uint8_t five[7] = {0x1f, 0x10, 0x10, 0x1e, 0x01, 0x01, 0x1e};
    static const uint8_t six[7] = {0x0e, 0x10, 0x10, 0x1e, 0x11, 0x11, 0x0e};
    static const uint8_t seven[7] = {0x1f, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08};
    static const uint8_t eight[7] = {0x0e, 0x11, 0x11, 0x0e, 0x11, 0x11, 0x0e};
    static const uint8_t nine[7] = {0x0e, 0x11, 0x11, 0x0f, 0x01, 0x01, 0x0e};

    switch (character) {
        case 'A': return a;
        case 'B': return b;
        case 'C': return c;
        case 'D': return d;
        case 'T': return t;
        case 'E': return e;
        case 'G': return g;
        case 'H': return h;
        case 'I': return i;
        case 'K': return k;
        case 'L': return l;
        case 'M': return m;
        case 'N': return n;
        case 'O': return o;
        case 'R': return r;
        case 'S': return s;
        case 'U': return u;
        case 'W': return w;
        case ':': return colon;
        case '0': return zero;
        case '1': return one;
        case '2': return two;
        case '3': return three;
        case '4': return four;
        case '5': return five;
        case '6': return six;
        case '7': return seven;
        case '8': return eight;
        case '9': return nine;
        default: return blank;
    }
}

static void append_vertex(
    float pixel_x,
    float pixel_y,
    float red,
    float green,
    float blue,
    float alpha
) {
    HudVertex *vertex;

    if (g_vertex_count >= HUD_MAX_VERTICES || g_width == 0 || g_height == 0)
        return;

    vertex = &g_vertices[g_vertex_count++];
    vertex->x = (pixel_x * 2.0f / (float) g_width) - 1.0f;
    vertex->y = 1.0f - (pixel_y * 2.0f / (float) g_height);
    vertex->red = red;
    vertex->green = green;
    vertex->blue = blue;
    vertex->alpha = alpha;
}

static void append_quad(
    float left,
    float top,
    float right,
    float bottom,
    float red,
    float green,
    float blue,
    float alpha
) {
    append_vertex(left, top, red, green, blue, alpha);
    append_vertex(right, top, red, green, blue, alpha);
    append_vertex(right, bottom, red, green, blue, alpha);
    append_vertex(left, top, red, green, blue, alpha);
    append_vertex(right, bottom, red, green, blue, alpha);
    append_vertex(left, bottom, red, green, blue, alpha);
}

static int build_text(char *text) {
    static const char waiting_text[] = "__HUD_WAITING_TEXT__";
    static const char counter_prefix[] = "__HUD_COUNTER_PREFIX__";
    int count = g_pending_count;
    int position = 0;
    int prefix_position = 0;

    if (!g_solo_ready) {
        while (waiting_text[position] != '\0') {
            text[position] = waiting_text[position];
            position++;
        }
        text[position] = '\0';
        return position;
    }

    if (count < 0)
        count = 0;
    if (count > 999)
        count = 999;

    while (counter_prefix[prefix_position] != '\0')
        text[position++] = counter_prefix[prefix_position++];

    if (count >= 100)
        text[position++] = (char) ('0' + (count / 100));
    if (count >= 10)
        text[position++] = (char) ('0' + ((count / 10) % 10));
    text[position++] = (char) ('0' + (count % 10));
    text[position] = '\0';
    return position;
}

static void append_text_pass(
    const char *text,
    int length,
    int origin_x,
    int origin_y,
    int scale,
    int shadow
) {
    int character_index;
    int row;
    int column;
    int offset = shadow ? 2 : 0;
    float red = shadow ? 0.0f : 1.0f;
    float green = shadow ? 0.0f : 0.58f;
    float blue = shadow ? 0.0f : 0.08f;
    float alpha = shadow ? 0.78f : 1.0f;

    for (character_index = 0; character_index < length; character_index++) {
        const uint8_t *rows = glyph_rows(text[character_index]);

        for (row = 0; row < 7; row++) {
            for (column = 0; column < 5; column++) {
                if ((rows[row] & (1u << (4 - column))) != 0) {
                    int left = origin_x + character_index * 6 * scale + column * scale + offset;
                    int top = origin_y + row * scale + offset;
                    append_quad(
                        (float) left,
                        (float) top,
                        (float) (left + scale),
                        (float) (top + scale),
                        red,
                        green,
                        blue,
                        alpha
                    );
                }
            }
        }
    }
}

static void generate_vertices(void) {
    static const char warning_text[] = "__HUD_WARNING_TEXT__";
    char text[24];
    int length = build_text(text);
    int warning_length = (int) text_length(warning_text);
    int scale;
    int margin;
    int text_width;
    int warning_width;
    int text_height;
    int block_height;
    int text_x;
    int warning_x;
    int y;
    int corner = g_corner;

    if (g_width >= 3000)
        scale = 5;
    else if (g_width >= 2200)
        scale = 4;
    else if (g_width >= 1200)
        scale = 3;
    else
        scale = 2;

    margin = 8 * scale;
    text_width = length * 6 * scale - scale;
    warning_width = warning_length * 6 * scale - scale;
    text_height = 7 * scale;
    block_height = text_height;

    if (g_warning)
        block_height += 3 * scale + text_height;

    if (corner < 0 || corner > 3)
        corner = 1;

    text_x = (corner == 1 || corner == 3)
        ? (int) g_width - margin - text_width
        : margin;
    warning_x = (corner == 1 || corner == 3)
        ? (int) g_width - margin - warning_width
        : margin;
    y = (corner == 2 || corner == 3)
        ? (int) g_height - margin - block_height
        : margin;

    if (text_x < 0) text_x = 0;
    if (warning_x < 0) warning_x = 0;
    if (y < 0) y = 0;

    g_vertex_count = 0;
    append_text_pass(text, length, text_x, y, scale, 1);
    append_text_pass(text, length, text_x, y, scale, 0);

    if (g_warning) {
        int warning_y = y + text_height + 3 * scale;
        append_text_pass(
            warning_text, warning_length, warning_x, warning_y, scale, 1
        );
        append_text_pass(
            warning_text, warning_length, warning_x, warning_y, scale, 0
        );
    }
}

#ifdef HUD_OFFLINE_TEST
size_t hud_test_state_size(void) {
    return sizeof(HudState);
}

int hud_test_generate(
    UINT width,
    UINT height,
    int pending_count,
    int corner,
    int warning,
    int solo_ready,
    float *average_x,
    float *average_y
) {
    UINT index;
    float sum_x = 0.0f;
    float sum_y = 0.0f;

    g_width = width;
    g_height = height;
    g_pending_count = pending_count;
    g_corner = corner;
    g_warning = warning;
    g_solo_ready = solo_ready;
    generate_vertices();

    for (index = 0; index < g_vertex_count; index++) {
        sum_x += g_vertices[index].x;
        sum_y += g_vertices[index].y;
    }

    if (average_x)
        *average_x = g_vertex_count ? sum_x / (float) g_vertex_count : 0.0f;
    if (average_y)
        *average_y = g_vertex_count ? sum_y / (float) g_vertex_count : 0.0f;

    return (int) g_vertex_count;
}
#endif

static void release_target_resources(void) {
    release_com(&g_command_list);
    release_com(&g_vertex_buffer);
    release_com(&g_render_target);
    g_width = 0;
    g_height = 0;
    g_dirty = 1;
}

static void release_all_resources(void) {
    release_target_resources();
    release_com(&g_rasterizer_state);
    release_com(&g_depth_state);
    release_com(&g_blend_state);
    release_com(&g_input_layout);
    release_com(&g_pixel_shader);
    release_com(&g_vertex_shader);
    release_com(&g_deferred_context);
    release_com(&g_immediate_context);
    release_com(&g_device);
    g_swap_chain = 0;
}

static HRESULT compile_shader(
    const char *source,
    const char *entry,
    const char *target,
    void **blob
) {
    void *errors = 0;
    HRESULT result = D3DCompile(
        source,
        text_length(source),
        0,
        0,
        0,
        entry,
        target,
        0,
        0,
        blob,
        &errors
    );
    release_com(&errors);
    return result;
}

static int create_pipeline_resources(void) {
    typedef void (*GetImmediateContextFunction)(void *, void **);
    typedef HRESULT (*CreateDeferredContextFunction)(void *, UINT, void **);
    typedef HRESULT (*CreateVertexShaderFunction)(void *, const void *, size_t, void *, void **);
    typedef HRESULT (*CreatePixelShaderFunction)(void *, const void *, size_t, void *, void **);
    typedef HRESULT (*CreateInputLayoutFunction)(void *, const D3d11InputElementDesc *, UINT, const void *, size_t, void **);
    typedef HRESULT (*CreateBlendStateFunction)(void *, const D3d11BlendDesc *, void **);
    typedef HRESULT (*CreateDepthStateFunction)(void *, const D3d11DepthStencilDesc *, void **);
    typedef HRESULT (*CreateRasterizerStateFunction)(void *, const D3d11RasterizerDesc *, void **);
    typedef void *(*BlobPointerFunction)(void *);
    typedef size_t (*BlobSizeFunction)(void *);

    void **device_table = vtable(g_device);
    void *vertex_blob = 0;
    void *pixel_blob = 0;
    void *vertex_code;
    void *pixel_code;
    size_t vertex_size;
    size_t pixel_size;
    HRESULT result;
    D3d11InputElementDesc elements[2];
    D3d11BlendDesc blend_desc;
    D3d11DepthStencilDesc depth_desc;
    D3d11RasterizerDesc rasterizer_desc;

    if (!device_table)
        return -20;

    ((GetImmediateContextFunction) device_table[40])(g_device, &g_immediate_context);
    if (!g_immediate_context)
        return -21;

    result = ((CreateDeferredContextFunction) device_table[27])(
        g_device, 0, &g_deferred_context
    );
    if (!succeeded(result) || !g_deferred_context)
        return -22;

    result = compile_shader(HUD_VERTEX_SHADER, "VSMain", "vs_4_0", &vertex_blob);
    if (!succeeded(result) || !vertex_blob)
        return -23;

    result = compile_shader(HUD_PIXEL_SHADER, "PSMain", "ps_4_0", &pixel_blob);
    if (!succeeded(result) || !pixel_blob) {
        release_com(&vertex_blob);
        return -24;
    }

    vertex_code = ((BlobPointerFunction) vtable(vertex_blob)[3])(vertex_blob);
    vertex_size = ((BlobSizeFunction) vtable(vertex_blob)[4])(vertex_blob);
    pixel_code = ((BlobPointerFunction) vtable(pixel_blob)[3])(pixel_blob);
    pixel_size = ((BlobSizeFunction) vtable(pixel_blob)[4])(pixel_blob);

    result = ((CreateVertexShaderFunction) device_table[12])(
        g_device, vertex_code, vertex_size, 0, &g_vertex_shader
    );
    if (!succeeded(result)) {
        release_com(&vertex_blob);
        release_com(&pixel_blob);
        return -25;
    }

    result = ((CreatePixelShaderFunction) device_table[15])(
        g_device, pixel_code, pixel_size, 0, &g_pixel_shader
    );
    if (!succeeded(result)) {
        release_com(&vertex_blob);
        release_com(&pixel_blob);
        return -26;
    }

    zero_memory(elements, sizeof(elements));
    elements[0].semantic_name = "POSITION";
    elements[0].format = 16;
    elements[0].aligned_byte_offset = 0;
    elements[1].semantic_name = "COLOR";
    elements[1].format = 2;
    elements[1].aligned_byte_offset = 8;

    result = ((CreateInputLayoutFunction) device_table[11])(
        g_device,
        elements,
        2,
        vertex_code,
        vertex_size,
        &g_input_layout
    );
    release_com(&vertex_blob);
    release_com(&pixel_blob);
    if (!succeeded(result))
        return -27;

    zero_memory(&blend_desc, sizeof(blend_desc));
    blend_desc.render_target[0].blend_enable = 1;
    blend_desc.render_target[0].source_blend = 5;
    blend_desc.render_target[0].destination_blend = 6;
    blend_desc.render_target[0].blend_operation = 1;
    blend_desc.render_target[0].source_blend_alpha = 2;
    blend_desc.render_target[0].destination_blend_alpha = 1;
    blend_desc.render_target[0].blend_operation_alpha = 1;
    blend_desc.render_target[0].render_target_write_mask = 0x0f;
    result = ((CreateBlendStateFunction) device_table[20])(
        g_device, &blend_desc, &g_blend_state
    );
    if (!succeeded(result))
        return -28;

    zero_memory(&depth_desc, sizeof(depth_desc));
    depth_desc.depth_enable = 0;
    depth_desc.depth_write_mask = 0;
    depth_desc.depth_function = 8;
    depth_desc.stencil_read_mask = 0xff;
    depth_desc.stencil_write_mask = 0xff;
    result = ((CreateDepthStateFunction) device_table[21])(
        g_device, &depth_desc, &g_depth_state
    );
    if (!succeeded(result))
        return -29;

    zero_memory(&rasterizer_desc, sizeof(rasterizer_desc));
    rasterizer_desc.fill_mode = 3;
    rasterizer_desc.cull_mode = 1;
    rasterizer_desc.depth_clip_enable = 1;
    result = ((CreateRasterizerStateFunction) device_table[22])(
        g_device, &rasterizer_desc, &g_rasterizer_state
    );
    if (!succeeded(result))
        return -30;

    return 1;
}

static int read_swap_chain_desc(void *swap_chain, DxgiSwapChainDesc *desc) {
    typedef HRESULT (*GetDescFunction)(void *, DxgiSwapChainDesc *);
    void **table = vtable(swap_chain);
    HRESULT result;

    if (!table)
        return 0;

    zero_memory(desc, sizeof(*desc));
    result = ((GetDescFunction) table[12])(swap_chain, desc);
    return succeeded(result) && desc->buffer_desc.width >= 320 &&
        desc->buffer_desc.height >= 200;
}

static int initialize_for_swap_chain(void *swap_chain, const DxgiSwapChainDesc *desc) {
    typedef HRESULT (*GetDeviceFunction)(void *, const Guid *, void **);
    void **table = vtable(swap_chain);
    HRESULT result;
    int pipeline_result;

    if (!table)
        return -10;

    g_swap_chain = swap_chain;
    result = ((GetDeviceFunction) table[7])(
        swap_chain, &IID_ID3D11_DEVICE, &g_device
    );
    if (!succeeded(result) || !g_device)
        return -11;

    pipeline_result = create_pipeline_resources();
    if (pipeline_result < 0)
        return pipeline_result;

    g_width = desc->buffer_desc.width;
    g_height = desc->buffer_desc.height;
    return 1;
}

static int create_render_target(void) {
    typedef HRESULT (*GetBufferFunction)(void *, UINT, const Guid *, void **);
    typedef HRESULT (*CreateRenderTargetFunction)(void *, void *, const void *, void **);
    void *back_buffer = 0;
    HRESULT result;

    if (!g_swap_chain || !g_device)
        return -31;

    result = ((GetBufferFunction) vtable(g_swap_chain)[9])(
        g_swap_chain, 0, &IID_ID3D11_TEXTURE2D, &back_buffer
    );
    if (!succeeded(result) || !back_buffer)
        return -32;

    result = ((CreateRenderTargetFunction) vtable(g_device)[9])(
        g_device, back_buffer, 0, &g_render_target
    );
    release_com(&back_buffer);
    if (!succeeded(result) || !g_render_target)
        return -33;

    return 1;
}

static int rebuild_command_list(void) {
    typedef HRESULT (*CreateBufferFunction)(void *, const D3d11BufferDesc *, const D3d11SubresourceData *, void **);
    typedef void (*IASetInputLayoutFunction)(void *, void *);
    typedef void (*IASetVertexBuffersFunction)(void *, UINT, UINT, void *const *, const UINT *, const UINT *);
    typedef void (*IASetTopologyFunction)(void *, UINT);
    typedef void (*SetShaderFunction)(void *, void *, void *const *, UINT);
    typedef void (*OMSetRenderTargetsFunction)(void *, UINT, void *const *, void *);
    typedef void (*OMSetBlendStateFunction)(void *, void *, const float *, UINT);
    typedef void (*OMSetDepthStateFunction)(void *, void *, UINT);
    typedef void (*RSSetStateFunction)(void *, void *);
    typedef void (*RSSetViewportsFunction)(void *, UINT, const D3d11Viewport *);
    typedef void (*DrawFunction)(void *, UINT, UINT);
    typedef HRESULT (*FinishCommandListFunction)(void *, BOOL, void **);

    D3d11BufferDesc buffer_desc;
    D3d11SubresourceData initial_data;
    D3d11Viewport viewport;
    void **device_table = vtable(g_device);
    void **context_table = vtable(g_deferred_context);
    void *render_target_array[1];
    void *vertex_buffer_array[1];
    UINT stride = (UINT) sizeof(HudVertex);
    UINT offset = 0;
    float blend_factor[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    HRESULT result;

    release_com(&g_command_list);
    release_com(&g_vertex_buffer);

    generate_vertices();
    if (g_vertex_count == 0)
        return -34;

    zero_memory(&buffer_desc, sizeof(buffer_desc));
    buffer_desc.byte_width = g_vertex_count * (UINT) sizeof(HudVertex);
    buffer_desc.usage = 1;
    buffer_desc.bind_flags = 1;
    zero_memory(&initial_data, sizeof(initial_data));
    initial_data.system_memory = g_vertices;

    result = ((CreateBufferFunction) device_table[3])(
        g_device, &buffer_desc, &initial_data, &g_vertex_buffer
    );
    if (!succeeded(result) || !g_vertex_buffer)
        return -35;

    render_target_array[0] = g_render_target;
    vertex_buffer_array[0] = g_vertex_buffer;
    zero_memory(&viewport, sizeof(viewport));
    viewport.width = (float) g_width;
    viewport.height = (float) g_height;
    viewport.maximum_depth = 1.0f;

    ((IASetInputLayoutFunction) context_table[17])(
        g_deferred_context, g_input_layout
    );
    ((IASetVertexBuffersFunction) context_table[18])(
        g_deferred_context, 0, 1, vertex_buffer_array, &stride, &offset
    );
    ((IASetTopologyFunction) context_table[24])(g_deferred_context, 4);
    ((SetShaderFunction) context_table[11])(
        g_deferred_context, g_vertex_shader, 0, 0
    );
    ((SetShaderFunction) context_table[9])(
        g_deferred_context, g_pixel_shader, 0, 0
    );
    ((SetShaderFunction) context_table[23])(g_deferred_context, 0, 0, 0);
    ((SetShaderFunction) context_table[60])(g_deferred_context, 0, 0, 0);
    ((SetShaderFunction) context_table[64])(g_deferred_context, 0, 0, 0);
    ((OMSetRenderTargetsFunction) context_table[33])(
        g_deferred_context, 1, render_target_array, 0
    );
    ((OMSetBlendStateFunction) context_table[35])(
        g_deferred_context, g_blend_state, blend_factor, 0xffffffffu
    );
    ((OMSetDepthStateFunction) context_table[36])(
        g_deferred_context, g_depth_state, 0
    );
    ((RSSetStateFunction) context_table[43])(
        g_deferred_context, g_rasterizer_state
    );
    ((RSSetViewportsFunction) context_table[44])(
        g_deferred_context, 1, &viewport
    );
    ((DrawFunction) context_table[13])(
        g_deferred_context, g_vertex_count, 0
    );

    result = ((FinishCommandListFunction) context_table[114])(
        g_deferred_context, 0, &g_command_list
    );
    if (!succeeded(result) || !g_command_list)
        return -36;

    g_dirty = 0;
    return 1;
}

static void execute_hud(void) {
    typedef void (*ExecuteCommandListFunction)(void *, void *, BOOL);
    if (!g_immediate_context || !g_command_list)
        return;

    ((ExecuteCommandListFunction) vtable(g_immediate_context)[58])(
        g_immediate_context, g_command_list, 1
    );
}

static void render_swap_chain(void *swap_chain) {
    DxgiSwapChainDesc desc;
    uint64_t area;
    uint64_t current_area;
    int result;

    if (!swap_chain || !g_enabled || g_in_render)
        return;

    if (!read_swap_chain_desc(swap_chain, &desc))
        return;

    area = (uint64_t) desc.buffer_desc.width * (uint64_t) desc.buffer_desc.height;
    current_area = (uint64_t) g_width * (uint64_t) g_height;

    if (g_swap_chain && swap_chain != g_swap_chain && current_area > 0 && area < current_area)
        return;

    if (g_failed_swap_chain == swap_chain)
        return;

    g_in_render = 1;

    if (swap_chain != g_swap_chain) {
        release_all_resources();
        result = initialize_for_swap_chain(swap_chain, &desc);
        if (result < 0) {
            g_status = result;
            g_failed_swap_chain = swap_chain;
            release_all_resources();
            g_in_render = 0;
            return;
        }
    }

    if (desc.buffer_desc.width != g_width || desc.buffer_desc.height != g_height) {
        release_target_resources();
        g_width = desc.buffer_desc.width;
        g_height = desc.buffer_desc.height;
    }

    if (!g_render_target) {
        result = create_render_target();
        if (result < 0) {
            g_status = result;
            g_in_render = 0;
            return;
        }
    }

    if (!g_command_list || g_dirty) {
        result = rebuild_command_list();
        if (result < 0) {
            g_status = result;
            g_in_render = 0;
            return;
        }
    }

    execute_hud();
    g_status = 1;
    g_in_render = 0;
}

void hud_probe_addresses(
    void **present_address,
    void **resize_buffers_address,
    void **resize_target_address
) {
    static const uint16_t static_class[] = {'S', 'T', 'A', 'T', 'I', 'C', 0};
    static const uint16_t window_title[] = {'T', 'H', 'H', 'U', 'D', 0};
    DxgiSwapChainDesc desc;
    void *window;
    void *swap_chain = 0;
    void *device = 0;
    void *context = 0;
    HRESULT result;
    void **table;

    if (present_address) *present_address = 0;
    if (resize_buffers_address) *resize_buffers_address = 0;
    if (resize_target_address) *resize_target_address = 0;

    if (g_present_address) {
        if (present_address) *present_address = g_present_address;
        if (resize_buffers_address) *resize_buffers_address = g_resize_buffers_address;
        if (resize_target_address) *resize_target_address = g_resize_target_address;
        return;
    }

    window = CreateWindowExW(
        0, static_class, window_title, 0x80000000u,
        0, 0, 2, 2, 0, 0, 0, 0
    );
    if (!window) {
        g_status = -1;
        return;
    }

    zero_memory(&desc, sizeof(desc));
    desc.buffer_desc.width = 2;
    desc.buffer_desc.height = 2;
    desc.buffer_desc.format = 28;
    desc.sample_desc.count = 1;
    desc.buffer_usage = 0x20;
    desc.buffer_count = 1;
    desc.output_window = window;
    desc.windowed = 1;
    desc.swap_effect = 0;

    result = D3D11CreateDeviceAndSwapChain(
        0, 1, 0, 0, 0, 0, 7, &desc,
        &swap_chain, &device, 0, &context
    );

    if (!succeeded(result)) {
        result = D3D11CreateDeviceAndSwapChain(
            0, 5, 0, 0, 0, 0, 7, &desc,
            &swap_chain, &device, 0, &context
        );
    }

    if (succeeded(result) && swap_chain) {
        table = vtable(swap_chain);
        g_present_address = table[8];
        g_resize_buffers_address = table[13];
        g_resize_target_address = table[14];
        g_status = 0;
    } else {
        g_status = -2;
    }

    release_com(&swap_chain);
    release_com(&context);
    release_com(&device);
    DestroyWindow(window);

    if (present_address) *present_address = g_present_address;
    if (resize_buffers_address) *resize_buffers_address = g_resize_buffers_address;
    if (resize_target_address) *resize_target_address = g_resize_target_address;
}

void hud_set_state(
    int enabled,
    int pending_count,
    int corner,
    int warning,
    int solo_ready
) {
    if (pending_count < 0) pending_count = 0;
    if (pending_count > 999) pending_count = 999;
    if (corner < 0 || corner > 3) corner = 1;

    if (g_pending_count != pending_count || g_corner != corner ||
        g_warning != (warning ? 1 : 0) ||
        g_solo_ready != (solo_ready ? 1 : 0))
        g_dirty = 1;

    g_pending_count = pending_count;
    g_corner = corner;
    g_warning = warning ? 1 : 0;
    g_solo_ready = solo_ready ? 1 : 0;
    g_enabled = enabled ? 1 : 0;
}

int hud_get_status(void) {
    return g_status;
}

void hud_present_on_enter(GumInvocationContext *context) {
    void *swap_chain = gum_invocation_context_get_nth_argument(context, 0);
    render_swap_chain(swap_chain);
}

void hud_resize_on_enter(GumInvocationContext *context) {
    void *swap_chain = gum_invocation_context_get_nth_argument(context, 0);
    if (swap_chain && swap_chain == g_swap_chain)
        release_target_resources();
}

void hud_shutdown(void) {
    g_enabled = 0;
    release_all_resources();
    g_failed_swap_chain = 0;
    g_status = 0;
    g_in_render = 0;
}
