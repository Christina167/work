#include "main.h"
#include "modbus_user.h"
#include <stdint.h>
#include "mb.h"        // 定义 eMBErrorCode、eMBRegisterMode
#include "mbport.h"    // 间接包含 port.h，其中定义了 UCHAR、USHORT 等
#define REG_INPUT_START   1U
#define REG_INPUT_NREGS   8U

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

    /* 地址0：固定测试值 */
    usInputBuf[0] = 0x1234U;

    /* 地址1：开机秒数，约每秒加1 */
    usInputBuf[1] = (USHORT)(now_ms / 1000U);

    /* 地址2和3：32位毫秒计数，高16位在前 */
    usInputBuf[2] = (USHORT)(now_ms >> 16);
    usInputBuf[3] = (USHORT)(now_ms & 0xFFFFU);

    /* 地址4：从站地址 */
    usInputBuf[4] = 1U;

    /* 地址5：波特率除以100，即9600/100=96 */
    usInputBuf[5] = 96U;

    usInputBuf[6] = 0U;
    usInputBuf[7] = 0U;
}
