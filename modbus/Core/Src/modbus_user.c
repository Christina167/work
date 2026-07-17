#include "main.h"
#include "modbus_user.h"
#include <stdint.h>
#include "mb.h"        // 定义 eMBErrorCode、eMBRegisterMode
#include "mbport.h"    // 间接包含 port.h，其中定义了 UCHAR、USHORT 等
#define REG_INPUT_START   1U
#define REG_INPUT_NREGS   10U

static USHORT usInputBuf[REG_INPUT_NREGS] = {0};
/*
 * FreeModbus 的寄存器回调地址是 1 基地址。
 *
 * Modbus 协议请求地址 0
 * 对应回调中的 usAddress = 1。
 */
#define REG_HOLDING_START   1U
#define REG_HOLDING_NREGS   16U

static USHORT usHoldingBuf[REG_HOLDING_NREGS] =
{
    100U, 200U, 300U, 400U,
    500U, 600U, 700U, 800U,
    0U,   0U,   0U,   0U,
    0U,   0U,   0U,   0U
};

eMBErrorCode eMBRegHoldingCB(UCHAR *pucRegBuffer,
                             USHORT usAddress,
                             USHORT usNRegs,
                             eMBRegisterMode eMode)
{
    USHORT usIndex;

    if (usAddress < REG_HOLDING_START)
    {
        return MB_ENOREG;
    }

    usIndex = (USHORT)(usAddress - REG_HOLDING_START);

    if (((uint32_t)usIndex + usNRegs) > REG_HOLDING_NREGS)
    {
        return MB_ENOREG;
    }

    while (usNRegs > 0U)
    {
        if (eMode == MB_REG_READ)
        {
            /*
             * Modbus 寄存器在线路上传输时，高字节在前。
             */
            *pucRegBuffer++ =
                (UCHAR)(usHoldingBuf[usIndex] >> 8);

            *pucRegBuffer++ =
                (UCHAR)(usHoldingBuf[usIndex] & 0xFFU);
        }
        else
        {
            usHoldingBuf[usIndex] =
                (USHORT)((USHORT)(*pucRegBuffer++) << 8);

            usHoldingBuf[usIndex] |=
                (USHORT)(*pucRegBuffer++);
        }

        usIndex++;
        usNRegs--;
    }

    return MB_ENOERR;
}

eMBErrorCode eMBRegInputCB(UCHAR *pucRegBuffer,
                           USHORT usAddress,
                           USHORT usNRegs)
{
    USHORT usIndex;

    if (usAddress < REG_INPUT_START)
    {
        return MB_ENOREG;
    }

    usIndex = (USHORT)(usAddress - REG_INPUT_START);

    if (((uint32_t)usIndex + usNRegs) > REG_INPUT_NREGS)
    {
        return MB_ENOREG;
    }

    while (usNRegs > 0U)
    {
        /* Modbus在线路上高字节在前 */
        *pucRegBuffer++ =
            (UCHAR)(usInputBuf[usIndex] >> 8);

        *pucRegBuffer++ =
            (UCHAR)(usInputBuf[usIndex] & 0xFFU);

        usIndex++;
        usNRegs--;
    }

    return MB_ENOERR;
}

eMBErrorCode eMBRegCoilsCB(UCHAR *pucRegBuffer,
                           USHORT usAddress,
                           USHORT usNCoils,
                           eMBRegisterMode eMode)
{
    (void)pucRegBuffer;
    (void)usAddress;
    (void)usNCoils;
    (void)eMode;

    return MB_ENOREG;
}

eMBErrorCode eMBRegDiscreteCB(UCHAR *pucRegBuffer,
                              USHORT usAddress,
                              USHORT usNDiscrete)
{
    (void)pucRegBuffer;
    (void)usAddress;
    (void)usNDiscrete;

    return MB_ENOREG;
}

void ModbusUser_UpdateInputRegisters(void)
{
    uint32_t now_ms = HAL_GetTick();

    uint16_t width_ticks;
    uint32_t pulse_count;
    uint8_t capture_valid;
    uint32_t primask;

    /*
     * 防止读取32位计数的过程中，
     * TIM2中断刚好更新测量结果。
     */
    primask = __get_PRIMASK();
    __disable_irq();

    width_ticks = g_capture_width_ticks;
    pulse_count = g_capture_pulse_count;
    capture_valid = g_capture_valid;

    __set_PRIMASK(primask);

    usInputBuf[0] = 0x1234U;
    usInputBuf[1] = (USHORT)(now_ms / 1000U);

    usInputBuf[2] = (USHORT)(now_ms >> 16);
    usInputBuf[3] = (USHORT)(now_ms & 0xFFFFU);

    usInputBuf[4] = 1U;
    usInputBuf[5] = 96U;

    /* 最新一次脉宽，单位tick */
    usInputBuf[6] = width_ticks;

    /* 有效脉冲总数，32位，高字在前 */
    usInputBuf[7] = (USHORT)(pulse_count >> 16);
    usInputBuf[8] = (USHORT)(pulse_count & 0xFFFFU);

    /* 0：尚未捕获；1：已经捕获过有效脉冲 */
    usInputBuf[9] = (USHORT)capture_valid;
}
